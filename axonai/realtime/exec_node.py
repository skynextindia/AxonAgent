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

    def __init__(self, daemons: Dict[str, object], host: str = "127.0.0.1", port: int = 8770):
        # Re-key by canonical 6-letter pair so a different broker suffix (e.g.
        # EURUSD.i vs EURUSDm) still routes correctly.
        self.daemons = {
            _canonical_symbol(getattr(d, "mt5_symbol", k)): d for k, d in daemons.items()
        }
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
        daemon = self.daemons.get(symbol)
        if daemon is None:
            logger.warning("ExecNodeServer: no daemon for symbol %r (cmd=%s)", symbol, cmd)
            return {"ok": False, "cmd": cmd, "reason": f"unknown symbol {symbol}"}
        try:
            if cmd == "enter":
                signal = req.get("signal", "")
                size_scale = float(req.get("size_scale", 1.0) or 1.0)
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
