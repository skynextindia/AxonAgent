"""Execution-node inbound server for the A→B order mirror (follower side).

Runs inside an execution-node process (``run.py --exec-node``). Receives entry
and close **decisions** from the lead brain over a WebSocket and routes them to
the local per-pair :class:`~axonai.realtime.daemon.AxonDaemon`, which executes
them with the engine's OWN order management — sizing to THIS terminal's equity
and resolving THIS broker's ticker/pip. The wire carries only
``{cmd, symbol, signal, size_scale, reason}``; never a price.

Binds to localhost by default: this is a live order-routing endpoint, so it must
not be exposed to the network unless deliberately intended.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Dict

from axonai.default_config import _canonical_symbol
from axonai.realtime.alerts import send_alert

logger = logging.getLogger(__name__)


def _drain_and_close(loop) -> None:
    """Cancel any pending tasks and close the loop without noisy teardown warnings."""
    if loop is None:
        return
    try:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    except Exception:
        pass
    try:
        loop.close()
    except Exception:
        pass


class ExecNodeServer:
    """Routes forwarded decisions to local daemons, keyed by canonical symbol."""

    def __init__(self, daemons: Dict[str, object], host: str = "127.0.0.1", port: int = 8770,
                 config: Dict = None):
        # Re-key by canonical 6-letter pair so a different broker suffix (e.g.
        # EURUSD.i vs EURUSDm) still routes correctly.
        self.daemons = {
            _canonical_symbol(getattr(d, "mt5_symbol", k)): d for k, d in daemons.items()
        }
        # Reconcile reads policy flags from here. Fall back to a daemon's config
        # (they all share one dict) so an older caller still gets the real flags
        # rather than silently defaulting.
        if config is None:
            for d in self.daemons.values():
                config = getattr(d, "config", None)
                if isinstance(config, dict):
                    break
        self.config = config if isinstance(config, dict) else {}
        self.host = host
        self.port = port
        self._running = False
        self._thread = None
        self._loop = None
        self._stop_event = None

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="ExecNodeServer", daemon=True)
        self._thread.start()
        logger.info(
            "ExecNodeServer: listening on ws://%s:%d for %d symbol(s): %s",
            self.host, self.port, len(self.daemons), ", ".join(self.daemons),
        )

    def stop(self) -> None:
        self._running = False
        loop, ev = self._loop, self._stop_event
        if loop and ev is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(ev.set)
            except Exception:
                pass

    # ── internals ──────────────────────────────────────────────────────────
    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as e:
            logger.error("ExecNodeServer loop error: %s", e, exc_info=True)
        finally:
            _drain_and_close(self._loop)

    async def _serve(self) -> None:
        from websockets.asyncio.server import serve as ws_serve
        self._stop_event = asyncio.Event()
        async with ws_serve(self._handle, self.host, self.port):
            logger.info("ExecNodeServer: WebSocket server started on port %d", self.port)
            await self._stop_event.wait()

    async def _handle(self, websocket) -> None:
        remote = getattr(websocket, "remote_address", "?")
        logger.info("ExecNodeServer: lead connected: %s", remote)
        try:
            async for message in websocket:
                try:
                    req = json.loads(message)
                except Exception:
                    continue
                # Run the MT5 order I/O off the event loop so a slow order_send
                # never stalls the socket.
                ack = await self._loop.run_in_executor(None, self._dispatch, req)
                try:
                    await websocket.send(json.dumps(ack))
                except Exception:
                    pass
        except Exception as e:
            logger.info("ExecNodeServer: lead disconnected (%s)", e)

    def _dispatch(self, req: dict) -> dict:
        cmd = (req.get("cmd") or "").lower()
        symbol = _canonical_symbol(req.get("symbol", ""))
        if cmd == "ping":
            return {"ok": True, "cmd": "pong"}
        if cmd == "sync":
            # Server-level: reconcile must reach EVERY daemon, not one symbol —
            # closing an orphan requires visiting the pairs the lead is flat on,
            # which are exactly the ones absent from the message.
            return self._reconcile(req)
        daemon = self.daemons.get(symbol)
        if daemon is None:
            logger.warning("ExecNodeServer: no daemon for symbol %r (cmd=%s)", symbol, cmd)
            return {"ok": False, "cmd": cmd, "reason": f"unknown symbol {symbol}"}
        try:
            if cmd == "enter":
                signal = req.get("signal", "")
                # Missing/malformed → 1.0, but a legitimate 0.0 must NOT become
                # full size: `float(x or 1.0)` treats 0.0 as falsy, so a lead
                # that ever sent size_scale=0 would have opened a FULL-size
                # position here — the exact opposite of the intent.
                raw_scale = req.get("size_scale", 1.0)
                try:
                    size_scale = 1.0 if raw_scale is None else float(raw_scale)
                except (TypeError, ValueError):
                    size_scale = 1.0
                if size_scale <= 0.0:
                    logger.info("ExecNodeServer: size_scale=%s on %s → entry declined",
                                raw_scale, symbol)
                    return {"ok": False, "cmd": cmd, "symbol": symbol,
                            "reason": "size_scale <= 0"}
                res = daemon.inject_signal(signal, size_scale, source="mirror")
                return {
                    "ok": bool(res), "cmd": cmd, "symbol": symbol,
                    "ticket": (res or {}).get("order"),
                    "volume": (res or {}).get("volume"),
                }
            if cmd == "close":
                reason = req.get("reason", "mirror close")
                n = daemon.inject_close(reason)
                return {"ok": True, "cmd": cmd, "symbol": symbol, "closed": n}
            return {"ok": False, "cmd": cmd, "reason": f"unknown cmd {cmd!r}"}
        except Exception as e:
            logger.error("ExecNodeServer: dispatch error for %s: %s", cmd, e, exc_info=True)
            return {"ok": False, "cmd": cmd, "reason": str(e)}

    # ── reconcile ──────────────────────────────────────────────────────────────
    def _reconcile(self, req: dict) -> dict:
        """Converge this node onto the lead's open-position snapshot.

        Runs on every (re)connect and catches whatever replay could not: entries
        that expired, decisions made before the lead process started, and stale
        positions left over from an earlier session.

        The two directions are **deliberately asymmetric**:

        * node holds a position the lead does NOT → **close it**. The lead is
          flat, so this is unmanaged risk sitting on the account; flattening is
          always safe and always correct.
        * lead holds a position the node does NOT → **alert only**. Filling it
          now means buying a move that has already happened, possibly one the
          lead is about to exit. Flat is fine on a challenge account;
          wrong-footed is not. Opt in with ``mirror_reconcile_enter``.
        * directions OPPOSE → **flatten, never flip.** Being on the wrong side is
          worse than being absent, but a blind reversal doubles the assumption.
        """
        lead_open = req.get("open")
        if not isinstance(lead_open, dict):
            lead_open = {}
        lead_open = {
            _canonical_symbol(k): (v if isinstance(v, dict) else {})
            for k, v in lead_open.items()
        }
        # Pairs the LEAD could not read. Absence from `open` means "lead is flat"
        # and authorises an orphan close, so an unverified pair must be named
        # here or we would close a position that is in fact matched.
        lead_unknown = {_canonical_symbol(s) for s in (req.get("unknown") or [])}
        auto_enter = bool(self.config.get("mirror_reconcile_enter", False))
        actions = []
        diverged = []

        for sym, daemon in self.daemons.items():
            if sym in lead_unknown:
                actions.append(f"{sym}: lead state UNKNOWN, skipped")
                logger.warning("ExecNodeServer: reconcile skipped %s — lead could not verify it", sym)
                continue
            lead_sig = lead_open.get(sym, {}).get("signal")
            try:
                state = daemon.mirror_position_state()
            except Exception as e:
                actions.append(f"{sym}: local read failed, skipped")
                logger.error("ExecNodeServer: reconcile read failed for %s: %s", sym, e)
                continue
            if not state.get("ok"):
                # Never guess: a terminal we cannot read, treated as flat, would
                # make the lead's position look like a divergence and — with
                # auto-enter on — double the real position.
                actions.append(f"{sym}: local state UNKNOWN, skipped")
                logger.warning("ExecNodeServer: reconcile skipped %s — cannot read local positions", sym)
                continue
            node_sig = state.get("signal")

            if node_sig and not lead_sig:
                n = daemon.inject_close("mirror reconcile: lead is flat")
                actions.append(f"{sym}: closed {n} orphan {node_sig}")
                logger.warning(
                    "ExecNodeServer: reconcile CLOSED orphan %s %s (%d position(s)) — lead is flat",
                    node_sig, sym, n,
                )
            elif lead_sig and not node_sig:
                if auto_enter:
                    res = daemon.inject_signal(lead_sig, 1.0, source="reconcile")
                    ok = bool(res)
                    actions.append(f"{sym}: reconcile-entered {lead_sig} ({'filled' if ok else 'no fill'})")
                    logger.warning(
                        "ExecNodeServer: reconcile ENTERED %s %s (mirror_reconcile_enter=ON) → %s",
                        lead_sig, sym, "ticket %s" % (res or {}).get("order") if ok else "no fill",
                    )
                else:
                    actions.append(f"{sym}: DIVERGED — lead holds {lead_sig}, node flat (no auto-enter)")
                    diverged.append(f"{sym} lead={lead_sig} node=flat")
                    logger.warning(
                        "ExecNodeServer: reconcile DIVERGENCE on %s — lead holds %s, this node is "
                        "flat. NOT entering (mirror_reconcile_enter=OFF): the signal has aged out. "
                        "This account will skip the trade; it re-syncs when the lead exits.",
                        sym, lead_sig,
                    )
            elif lead_sig and node_sig and lead_sig != node_sig:
                n = daemon.inject_close(f"mirror reconcile: direction mismatch (lead {lead_sig})")
                actions.append(f"{sym}: MISMATCH lead={lead_sig} node={node_sig} → closed {n}")
                diverged.append(f"{sym} lead={lead_sig} node={node_sig} (flattened)")
                logger.error(
                    "ExecNodeServer: reconcile MISMATCH on %s — lead %s vs node %s. Flattened %d "
                    "position(s); not flipping.", sym, lead_sig, node_sig, n,
                )
            else:
                actions.append(f"{sym}: in sync ({node_sig or 'flat'})")

        logger.info("ExecNodeServer: reconcile — %s", "; ".join(actions) or "no daemons")
        if diverged:
            try:
                send_alert(
                    "AxonAi exec-node: mirror reconcile found divergence — "
                    + "; ".join(diverged), self.config,
                )
            except Exception as e:
                logger.error("ExecNodeServer: reconcile alert failed: %s", e)
        return {"ok": True, "cmd": "sync", "actions": actions}
