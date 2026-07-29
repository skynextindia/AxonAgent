"""Best-effort order-decision forwarder for the A→B execution mirror (lead side).

The lead ("brain") daemon uses this to push each entry/close **decision** — never
a price — to a second, execution-only node (see :mod:`axonai.realtime.exec_node`)
over a WebSocket. It is **fail-open**: an unreachable or dropped node NEVER blocks
or breaks the lead's own trading.

Only ``{cmd, symbol, signal, size_scale, reason}`` crosses the wire, so a
different-broker execution node re-derives ticker, digits, pip value, ATR SL/TP
and lot size entirely from its own terminal.

Durability — decisions made while the node is down are **queued**, not lost:

* **Entries expire** (``mirror_entry_ttl_seconds``). The edge is a microstructure
  reversal that is gone in seconds; replaying a minutes-old entry is not catching
  up, it opens a brand-new unvetted trade at a price the lead never signalled on.
* **Closes never expire.** Flattening is always safe and always correct.
* **enter+close cancel out.** If the round trip completed while the node was down
  it never opened, so there is nothing to close — replaying both would open a
  position purely to pay the spread closing it.
* **Reconcile is authoritative.** On every (re)connect the lead sends a ``sync``
  snapshot of its open positions so the node can converge (see
  :meth:`ExecNodeServer._reconcile`), catching whatever the queue could not.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque

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


class MirrorClient:
    """WebSocket decision forwarder to the execution node, with replay + reconcile.

    Runs its asyncio client in a daemon thread with auto-reconnect. ``send()`` is
    safe to call from any thread (the daemon event-loop / tick threads) and never
    raises.

    ``snapshot_provider`` is an optional zero-arg callable returning the reconcile
    body ``{"open": {"EURUSD": {"signal": "Buy"}, ...}, "unknown": ["USDJPY"]}``.
    Symbols absent from ``open`` are ones the lead is flat on — that absence is
    what authorises the node to close an orphan, so a pair whose state could NOT
    be read must be listed in ``unknown`` rather than merely omitted.
    """

    def __init__(self, url: str = "ws://127.0.0.1:8770",
                 auto_reconnect: bool = True, reconnect_delay: float = 3.0,
                 queue_max: int = 200, entry_ttl: float = 45.0,
                 snapshot_provider=None):
        self.url = url
        self.auto_reconnect = auto_reconnect
        self.reconnect_delay = reconnect_delay
        self.entry_ttl = float(entry_ttl)
        self.snapshot_provider = snapshot_provider
        self._running = False
        self._thread = None
        self._loop = None
        self._ws = None
        # Bounded so a node that is down all day cannot grow this without limit;
        # replay is for a reconnect gap, and `sync` covers the rest.
        self._queue = deque(maxlen=max(1, int(queue_max)))
        self._qlock = threading.Lock()

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="MirrorClient", daemon=True)
        self._thread.start()
        logger.info("MirrorClient: forwarding trade decisions to %s", self.url)

    def stop(self) -> None:
        self._running = False
        loop = self._loop
        if loop and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._request_close)
            except Exception:
                pass

    def _request_close(self) -> None:
        """Close the live socket from the loop thread so _connect_loop returns."""
        ws = self._ws
        if ws is not None:
            try:
                asyncio.create_task(ws.close())
            except Exception:
                pass

    def is_connected(self) -> bool:
        return self._ws is not None and self._running

    # ── internals ──────────────────────────────────────────────────────────
    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_loop())
        except Exception as e:
            logger.debug("MirrorClient loop ended: %s", e)
        finally:
            _drain_and_close(self._loop)

    async def _connect_loop(self) -> None:
        import websockets
        while self._running:
            try:
                async with websockets.connect(
                    self.url, ping_interval=20, ping_timeout=10
                ) as ws:
                    self._ws = ws
                    logger.info("MirrorClient: connected to execution node at %s", self.url)
                    # Replay BEFORE reconcile: a fresh queued entry then shows up
                    # as a matching position in the snapshot instead of as a
                    # divergence the node would only be able to warn about.
                    await self._flush_queue()
                    await self._send_sync()
                    # Drain acks from the node (informational only).
                    async for msg in ws:
                        try:
                            logger.info("MirrorClient: exec-node ack: %s", json.loads(msg))
                        except Exception:
                            pass
            except Exception as e:
                if self._running:
                    logger.warning(
                        "MirrorClient: execution node unreachable (%s); retrying in %.0fs",
                        e, self.reconnect_delay,
                    )
                    await asyncio.sleep(self.reconnect_delay)
                if not self.auto_reconnect:
                    break
            finally:
                self._ws = None

    def send(self, payload: dict) -> bool:
        """Forward a decision to the execution node. Never raises.

        When the node is unreachable the decision is QUEUED for replay on
        reconnect rather than dropped, so a lead exit can never leave a live
        position stranded on the follower account. Returns True only if the
        decision went out on the wire; a queued decision returns False.
        """
        if not self._running or not self._loop:
            logger.warning("MirrorClient: not running; decision NOT mirrored: %s", payload)
            return False
        if self._ws is None:
            self._enqueue(payload)
            return False
        try:
            asyncio.run_coroutine_threadsafe(self._ws_send(payload), self._loop)
            return True
        except Exception as e:
            logger.warning("MirrorClient: send failed, queued for replay: %s", e)
            self._enqueue(payload)
            return False

    # ── replay queue ───────────────────────────────────────────────────────────
    def _enqueue(self, payload: dict) -> None:
        """Hold a decision for replay on reconnect. Never raises.

        Timestamps are ``time.monotonic()`` and TTL is applied on the LEAD side,
        so a clock difference between the two processes can never age a decision
        wrongly.
        """
        cmd = (payload.get("cmd") or "").lower()
        if cmd not in ("enter", "close"):
            return  # pings and unknown verbs are not worth replaying
        symbol = payload.get("symbol", "")
        try:
            with self._qlock:
                if cmd == "close":
                    stale = [
                        i for i, (_, p) in enumerate(self._queue)
                        if (p.get("cmd") or "").lower() == "enter"
                        and p.get("symbol", "") == symbol
                    ]
                    if stale:
                        # The whole round trip happened while the node was down:
                        # it never opened, so there is nothing to close. Replaying
                        # both would open a position purely to pay the spread
                        # closing it.
                        for i in reversed(stale):
                            del self._queue[i]
                        logger.info(
                            "MirrorClient: queued enter+close on %s cancelled out "
                            "(round trip completed while node was down); neither replayed",
                            symbol,
                        )
                        return
                dropping = len(self._queue) == self._queue.maxlen
                self._queue.append((time.monotonic(), payload))
                depth = len(self._queue)
            logger.warning(
                "MirrorClient: node offline — %s %s QUEUED for replay (depth %d)%s",
                cmd, symbol, depth,
                " — queue full, oldest decision discarded" if dropping else "",
            )
        except Exception as e:
            logger.error("MirrorClient: enqueue failed, decision LOST: %s", e)

    async def _flush_queue(self) -> None:
        """Replay queued decisions in order, expiring stale entries. Loop thread."""
        with self._qlock:
            items = list(self._queue)
            self._queue.clear()
        if not items:
            return
        now = time.monotonic()
        sent = expired = failed = 0
        for ts, payload in items:
            cmd = (payload.get("cmd") or "").lower()
            age = now - ts
            if cmd == "enter" and age > self.entry_ttl:
                expired += 1
                logger.warning(
                    "MirrorClient: DROPPED stale replay enter %s — %.0fs old (TTL %.0fs). "
                    "The node was down when the lead entered; reconcile reports the "
                    "divergence instead of chasing a price the signal no longer supports.",
                    payload.get("symbol"), age, self.entry_ttl,
                )
                continue
            if await self._ws_send_raw(json.dumps(payload)):
                sent += 1
            else:
                failed += 1
                self._enqueue(payload)  # connection died mid-flush; keep it
        logger.info(
            "MirrorClient: replay complete — %d sent, %d expired, %d re-queued.",
            sent, expired, failed,
        )

    async def _send_sync(self) -> None:
        """Push the lead's open-position snapshot so the node can converge."""
        provider = self.snapshot_provider
        if provider is None:
            return
        try:
            body = provider() or {}
            if not isinstance(body, dict):
                raise TypeError(f"snapshot must be a dict, got {type(body).__name__}")
        except Exception as e:
            logger.warning("MirrorClient: snapshot provider failed; NO reconcile sent: %s", e)
            return
        payload = {"cmd": "sync"}
        payload.update(body)
        if await self._ws_send_raw(json.dumps(payload)):
            logger.info(
                "MirrorClient: reconcile snapshot sent — open=%s unknown=%s",
                body.get("open") or "none (lead flat on all pairs)",
                body.get("unknown") or "none",
            )

    # ── wire ───────────────────────────────────────────────────────────────────
    async def _ws_send(self, payload: dict) -> None:
        """Send one live decision; queue it for replay if the socket has gone."""
        if not await self._ws_send_raw(json.dumps(payload)):
            self._enqueue(payload)

    async def _ws_send_raw(self, msg: str) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            await ws.send(msg)
            return True
        except Exception as e:
            logger.warning("MirrorClient: ws send error (non-fatal): %s", e)
            return False
