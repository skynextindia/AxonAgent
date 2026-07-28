"""Best-effort order-decision forwarder for the A→B execution mirror (lead side).

The lead ("brain") daemon uses this to push each entry/close **decision** — never
a price — to a second, execution-only node (see :mod:`axonai.realtime.exec_node`)
over a WebSocket. It is deliberately fire-and-forget and **fail-open**: an
unreachable or dropped node NEVER blocks or breaks the lead's own trading; the
decision is simply logged as "not mirrored" and the lead carries on.

Only ``{cmd, symbol, signal, size_scale, reason}`` crosses the wire, so a
different-broker execution node re-derives ticker, digits, pip value, ATR SL/TP
and lot size entirely from its own terminal.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading

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
    """Fire-and-forget WebSocket forwarder to the execution node.

    Runs its asyncio client in a daemon thread with auto-reconnect. ``send()`` is
    safe to call from any thread (the daemon event-loop / tick threads) and never
    raises.
    """

    def __init__(self, url: str = "ws://127.0.0.1:8770",
                 auto_reconnect: bool = True, reconnect_delay: float = 3.0):
        self.url = url
        self.auto_reconnect = auto_reconnect
        self.reconnect_delay = reconnect_delay
        self._running = False
        self._thread = None
        self._loop = None
        self._ws = None

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
        """Fire-and-forget a decision to the execution node. Never raises."""
        if not self._running or not self._loop:
            logger.warning("MirrorClient: not running; decision NOT mirrored: %s", payload)
            return False
        try:
            msg = json.dumps(payload)
            asyncio.run_coroutine_threadsafe(self._ws_send(msg), self._loop)
            return True
        except Exception as e:
            logger.warning("MirrorClient: send failed (non-fatal): %s", e)
            return False

    async def _ws_send(self, msg: str) -> None:
        ws = self._ws
        if ws is None:
            logger.warning("MirrorClient: no live connection; decision dropped: %s", msg)
            return
        try:
            await ws.send(msg)
        except Exception as e:
            logger.warning("MirrorClient: ws send error (non-fatal): %s", e)
