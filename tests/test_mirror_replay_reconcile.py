"""Tests for mirror replay + reconcile (no MT5, no sockets).

The mirror used to be pure fire-and-forget: a decision made while the execution
node was down was lost permanently and the two accounts silently diverged. These
tests pin the durability rules that fixed it — and, just as importantly, the
rules that keep replay from being WORSE than dropping:

  * entries EXPIRE past the TTL (a minutes-late entry is a new unvetted trade)
  * closes NEVER expire (flattening is always safe)
  * enter+close while offline cancel out (the round trip never reached the node)
  * the queue is bounded (a node down all day cannot grow it without limit)
  * an in-flight send that fails re-queues instead of vanishing
  * reconcile closes orphans, only ALERTS on a missing entry (opt-in to fill)
  * reconcile refuses to act on ANY unverified state, either side

``MirrorClient`` is driven directly with a stub socket rather than through a real
WebSocket, so the queue/TTL/supersede logic is tested without any I/O.
"""

from __future__ import annotations

import asyncio
import json
import logging
import unittest

from axonai.realtime.exec_node import ExecNodeServer
from axonai.realtime.mirror_client import MirrorClient


class _StubWS:
    """Minimal websocket stand-in: records sends, can be made to fail."""

    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send(self, msg):
        if self.fail:
            raise ConnectionError("socket closed")
        self.sent.append(json.loads(msg))


def _client(**kw):
    kw.setdefault("entry_ttl", 45.0)
    mc = MirrorClient("ws://127.0.0.1:9999", **kw)
    # send() short-circuits unless it believes the loop thread is up; we drive
    # the coroutines by hand instead of starting a thread.
    mc._running = True
    mc._loop = object()
    return mc


def _cmds(ws):
    return [(p.get("cmd"), p.get("symbol") or p.get("signal")) for p in ws.sent]


class TestOfflineQueueing(unittest.TestCase):
    def test_send_while_offline_queues_instead_of_dropping(self):
        mc = _client()
        self.assertIsNone(mc._ws)
        ok = mc.send({"cmd": "enter", "symbol": "EURUSD", "signal": "Buy"})
        self.assertFalse(ok, "a queued decision must not report as sent")
        self.assertEqual(len(mc._queue), 1)

    def test_close_while_offline_is_queued(self):
        mc = _client()
        mc.send({"cmd": "close", "symbol": "EURUSD", "reason": "SL"})
        self.assertEqual(len(mc._queue), 1)

    def test_ping_is_not_queued(self):
        # Replaying a stale heartbeat is pointless; only orders matter.
        mc = _client()
        mc.send({"cmd": "ping"})
        self.assertEqual(len(mc._queue), 0)

    def test_queue_is_bounded(self):
        mc = _client(queue_max=3)
        for i in range(10):
            mc.send({"cmd": "close", "symbol": "EURUSD", "reason": f"r{i}"})
        self.assertEqual(len(mc._queue), 3)
        # Oldest discarded, newest kept.
        self.assertEqual([p["reason"] for _, p in mc._queue], ["r7", "r8", "r9"])


class TestSupersede(unittest.TestCase):
    def test_enter_then_close_offline_cancel_out(self):
        # The whole round trip completed while the node was down: it never
        # opened, so replaying both would open a position purely to close it.
        mc = _client()
        mc.send({"cmd": "enter", "symbol": "EURUSD", "signal": "Buy"})
        mc.send({"cmd": "close", "symbol": "EURUSD", "reason": "trail stop"})
        self.assertEqual(len(mc._queue), 0)

    def test_supersede_is_per_symbol(self):
        mc = _client()
        mc.send({"cmd": "enter", "symbol": "EURUSD", "signal": "Buy"})
        mc.send({"cmd": "enter", "symbol": "USDJPY", "signal": "Sell"})
        mc.send({"cmd": "close", "symbol": "EURUSD", "reason": "SL"})
        # Only the EURUSD pair cancelled; USDJPY's entry survives.
        self.assertEqual([p["symbol"] for _, p in mc._queue], ["USDJPY"])

    def test_close_with_no_pending_enter_is_kept(self):
        # Position opened BEFORE the node went down: the close must still land.
        mc = _client()
        mc.send({"cmd": "close", "symbol": "EURUSD", "reason": "EOD flat"})
        self.assertEqual(len(mc._queue), 1)


class TestReplayTTL(unittest.TestCase):
    def _flush(self, mc, ws):
        mc._ws = ws
        asyncio.run(mc._flush_queue())

    def test_fresh_entry_is_replayed(self):
        mc = _client(entry_ttl=45.0)
        mc.send({"cmd": "enter", "symbol": "EURUSD", "signal": "Buy"})
        ws = _StubWS()
        self._flush(mc, ws)
        self.assertEqual(_cmds(ws), [("enter", "EURUSD")])
        self.assertEqual(len(mc._queue), 0)

    def test_stale_entry_is_dropped(self):
        # A 15-minute-late entry is not "catching up" — it is a brand-new
        # unvetted trade at a price the lead never signalled on.
        mc = _client(entry_ttl=45.0)
        mc.send({"cmd": "enter", "symbol": "EURUSD", "signal": "Buy"})
        ts, payload = mc._queue[0]
        mc._queue[0] = (ts - 900.0, payload)
        ws = _StubWS()
        with self.assertLogs("axonai.realtime.mirror_client", level="WARNING") as cm:
            self._flush(mc, ws)
        self.assertEqual(ws.sent, [])
        self.assertTrue(any("DROPPED stale replay" in m for m in cm.output))

    def test_stale_close_is_still_replayed(self):
        # Closes never expire: flattening is always safe and always correct.
        mc = _client(entry_ttl=45.0)
        mc.send({"cmd": "close", "symbol": "EURUSD", "reason": "SL hit"})
        ts, payload = mc._queue[0]
        mc._queue[0] = (ts - 7200.0, payload)
        ws = _StubWS()
        self._flush(mc, ws)
        self.assertEqual(_cmds(ws), [("close", "EURUSD")])

    def test_replay_preserves_order(self):
        mc = _client()
        mc.send({"cmd": "close", "symbol": "EURUSD", "reason": "flat first"})
        mc.send({"cmd": "enter", "symbol": "EURUSD", "signal": "Sell"})
        ws = _StubWS()
        self._flush(mc, ws)
        self.assertEqual([p["cmd"] for p in ws.sent], ["close", "enter"])

    def test_failed_flush_requeues(self):
        mc = _client()
        mc.send({"cmd": "close", "symbol": "EURUSD", "reason": "SL"})
        self._flush(mc, _StubWS(fail=True))
        self.assertEqual(len(mc._queue), 1, "a decision must survive a mid-flush drop")

    def test_inflight_send_failure_requeues(self):
        # The socket can die between send()'s check and the actual write.
        mc = _client()
        mc._ws = _StubWS(fail=True)
        asyncio.run(mc._ws_send({"cmd": "close", "symbol": "EURUSD", "reason": "SL"}))
        self.assertEqual(len(mc._queue), 1)


class TestSyncEmission(unittest.TestCase):
    def test_snapshot_is_sent_as_sync(self):
        body = {"open": {"EURUSD": {"signal": "Buy"}}, "unknown": ["USDJPY"]}
        mc = _client(snapshot_provider=lambda: body)
        ws = _StubWS()
        mc._ws = ws
        asyncio.run(mc._send_sync())
        self.assertEqual(ws.sent, [{"cmd": "sync", **body}])

    def test_no_provider_sends_nothing(self):
        mc = _client()
        ws = _StubWS()
        mc._ws = ws
        asyncio.run(mc._send_sync())
        self.assertEqual(ws.sent, [])

    def test_broken_provider_sends_no_sync(self):
        # A snapshot we cannot build must not become an empty one: the node
        # reads "no open positions" as authorisation to close everything.
        def boom():
            raise RuntimeError("MT5 down")
        mc = _client(snapshot_provider=boom)
        ws = _StubWS()
        mc._ws = ws
        with self.assertLogs("axonai.realtime.mirror_client", level="WARNING"):
            asyncio.run(mc._send_sync())
        self.assertEqual(ws.sent, [])

    def test_non_dict_provider_sends_no_sync(self):
        mc = _client(snapshot_provider=lambda: ["EURUSD"])
        ws = _StubWS()
        mc._ws = ws
        with self.assertLogs("axonai.realtime.mirror_client", level="WARNING"):
            asyncio.run(mc._send_sync())
        self.assertEqual(ws.sent, [])


class _NodeDaemon:
    """Exec-node daemon stub with a settable local position state."""

    def __init__(self, symbol="EURUSD", state=None):
        self.mt5_symbol = symbol
        self.config = {}
        self._state = state if state is not None else {"ok": True, "signal": None}
        self.entered = []
        self.closed = []

    def mirror_position_state(self):
        return self._state

    def inject_signal(self, signal, size_scale=1.0, source="mirror"):
        self.entered.append((signal, size_scale, source))
        return {"order": 999, "volume": 1.0}

    def inject_close(self, reason):
        self.closed.append(reason)
        return 1


def _server(daemons, config=None):
    return ExecNodeServer(daemons, port=0, config=config or {})


class TestReconcile(unittest.TestCase):
    def test_orphan_on_node_is_closed(self):
        # Lead is flat, node holds a position → unmanaged risk. Always close.
        d = _NodeDaemon(state={"ok": True, "signal": "Buy", "count": 1})
        ack = _server({"EURUSD": d})._dispatch({"cmd": "sync", "open": {}})
        self.assertTrue(ack["ok"])
        self.assertEqual(len(d.closed), 1)
        self.assertIn("lead is flat", d.closed[0])

    def test_lead_position_missing_on_node_alerts_but_does_not_enter(self):
        d = _NodeDaemon(state={"ok": True, "signal": None})
        with self.assertLogs("axonai.realtime.exec_node", level="WARNING") as cm:
            ack = _server({"EURUSD": d})._dispatch(
                {"cmd": "sync", "open": {"EURUSD": {"signal": "Buy"}}})
        self.assertEqual(d.entered, [], "must not chase an aged-out signal by default")
        self.assertEqual(d.closed, [])
        self.assertTrue(any("DIVERGENCE" in m for m in cm.output))
        self.assertTrue(any("DIVERGED" in a for a in ack["actions"]))

    def test_lead_position_missing_enters_when_opted_in(self):
        d = _NodeDaemon(state={"ok": True, "signal": None})
        srv = _server({"EURUSD": d}, config={"mirror_reconcile_enter": True})
        srv._dispatch({"cmd": "sync", "open": {"EURUSD": {"signal": "Sell"}}})
        self.assertEqual(d.entered, [("Sell", 1.0, "reconcile")])

    def test_direction_mismatch_flattens_and_never_flips(self):
        d = _NodeDaemon(state={"ok": True, "signal": "Sell", "count": 1})
        ack = _server({"EURUSD": d})._dispatch(
            {"cmd": "sync", "open": {"EURUSD": {"signal": "Buy"}}})
        self.assertEqual(len(d.closed), 1)
        self.assertEqual(d.entered, [], "a blind reversal doubles the assumption")
        self.assertTrue(any("MISMATCH" in a for a in ack["actions"]))

    def test_matching_position_is_left_alone(self):
        d = _NodeDaemon(state={"ok": True, "signal": "Buy", "count": 1})
        ack = _server({"EURUSD": d})._dispatch(
            {"cmd": "sync", "open": {"EURUSD": {"signal": "Buy"}}})
        self.assertEqual(d.closed, [])
        self.assertEqual(d.entered, [])
        self.assertTrue(any("in sync" in a for a in ack["actions"]))

    def test_unreadable_node_state_is_never_acted_on(self):
        # Treating an unreadable terminal as flat would make the lead's position
        # look like a divergence and, with auto-enter on, double the position.
        d = _NodeDaemon(state={"ok": False})
        srv = _server({"EURUSD": d}, config={"mirror_reconcile_enter": True})
        ack = srv._dispatch({"cmd": "sync", "open": {"EURUSD": {"signal": "Buy"}}})
        self.assertEqual(d.entered, [])
        self.assertEqual(d.closed, [])
        self.assertTrue(any("UNKNOWN" in a for a in ack["actions"]))

    def test_lead_unknown_symbol_blocks_the_orphan_close(self):
        # The lead could not verify EURUSD, so its absence from `open` does NOT
        # mean flat — closing here would kill a position that is really matched.
        d = _NodeDaemon(state={"ok": True, "signal": "Buy", "count": 1})
        ack = _server({"EURUSD": d})._dispatch(
            {"cmd": "sync", "open": {}, "unknown": ["EURUSD"]})
        self.assertEqual(d.closed, [])
        self.assertTrue(any("lead state UNKNOWN" in a for a in ack["actions"]))

    def test_reconcile_visits_every_pair_not_just_one(self):
        eur = _NodeDaemon("EURUSD.i", {"ok": True, "signal": "Buy", "count": 1})
        jpy = _NodeDaemon("USDJPY.i", {"ok": True, "signal": "Sell", "count": 1})
        srv = _server({"EURUSD.i": eur, "USDJPY.i": jpy})
        srv._dispatch({"cmd": "sync", "open": {"EURUSD": {"signal": "Buy"}}})
        self.assertEqual(eur.closed, [])          # matched
        self.assertEqual(len(jpy.closed), 1)      # orphan on a pair the lead omitted

    def test_broker_suffix_is_canonicalised_both_ways(self):
        d = _NodeDaemon("EURUSD.i", {"ok": True, "signal": "Buy", "count": 1})
        ack = _server({"EURUSD.i": d})._dispatch(
            {"cmd": "sync", "open": {"EURUSD.i": {"signal": "Buy"}}})
        self.assertTrue(any("in sync" in a for a in ack["actions"]))

    def test_malformed_sync_payload_does_not_crash(self):
        d = _NodeDaemon(state={"ok": True, "signal": None})
        for bad in ({"cmd": "sync"},
                    {"cmd": "sync", "open": None},
                    {"cmd": "sync", "open": "EURUSD"},
                    {"cmd": "sync", "open": {"EURUSD": None}},
                    {"cmd": "sync", "unknown": None}):
            ack = _server({"EURUSD": d})._dispatch(bad)
            self.assertTrue(ack["ok"], f"crashed on {bad!r}")

    def test_reconcile_survives_a_raising_daemon(self):
        class _Boom(_NodeDaemon):
            def mirror_position_state(self):
                raise RuntimeError("terminal gone")
        good = _NodeDaemon("USDJPY", {"ok": True, "signal": None})
        srv = _server({"EURUSD": _Boom("EURUSD"), "USDJPY": good})
        ack = srv._dispatch({"cmd": "sync", "open": {}})
        self.assertTrue(ack["ok"])
        self.assertEqual(len(ack["actions"]), 2, "one bad pair must not skip the rest")


class TestLeadSnapshot(unittest.TestCase):
    """The lead half of the contract: absence from `open` authorises a close."""

    def _snapshot(self, daemons):
        from axonai.realtime.supervisor import DaemonSupervisor
        s = DaemonSupervisor.__new__(DaemonSupervisor)   # no MT5 connect
        s.daemons = daemons
        return s._mirror_snapshot()

    def test_open_positions_are_reported_with_direction(self):
        snap = self._snapshot({
            "EURUSD": _NodeDaemon("EURUSD.i", {"ok": True, "signal": "Buy", "count": 1}),
            "USDJPY": _NodeDaemon("USDJPY.i", {"ok": True, "signal": "Sell", "count": 1}),
        })
        self.assertEqual(snap["open"],
                         {"EURUSD": {"signal": "Buy"}, "USDJPY": {"signal": "Sell"}})
        self.assertEqual(snap["unknown"], [])

    def test_flat_pair_is_simply_absent(self):
        snap = self._snapshot({"EURUSD": _NodeDaemon("EURUSD.i", {"ok": True, "signal": None})})
        self.assertEqual(snap["open"], {})
        self.assertEqual(snap["unknown"], [])

    def test_unverified_pair_goes_to_unknown_not_silently_omitted(self):
        # This is the whole point: omitting it would read as "lead is flat" and
        # the node would close a position that is actually matched.
        snap = self._snapshot({"EURUSD": _NodeDaemon("EURUSD.i", {"ok": False})})
        self.assertEqual(snap["open"], {})
        self.assertEqual(snap["unknown"], ["EURUSD"])

    def test_raising_daemon_goes_to_unknown(self):
        class _Boom(_NodeDaemon):
            def mirror_position_state(self):
                raise RuntimeError("terminal gone")
        snap = self._snapshot({"EURUSD": _Boom("EURUSD.i")})
        self.assertEqual(snap["unknown"], ["EURUSD"])

    def test_snapshot_round_trips_into_reconcile_as_a_no_op(self):
        # Lead and node both long EURUSD, both flat USDJPY → nothing must move.
        lead = {
            "EURUSD": _NodeDaemon("EURUSD.i", {"ok": True, "signal": "Buy", "count": 1}),
            "USDJPY": _NodeDaemon("USDJPY.i", {"ok": True, "signal": None}),
        }
        node_eur = _NodeDaemon("EURUSD", {"ok": True, "signal": "Buy", "count": 1})
        node_jpy = _NodeDaemon("USDJPY", {"ok": True, "signal": None})
        srv = _server({"EURUSD": node_eur, "USDJPY": node_jpy})
        ack = srv._dispatch({"cmd": "sync", **self._snapshot(lead)})
        self.assertTrue(all("in sync" in a for a in ack["actions"]), ack["actions"])
        self.assertEqual(node_eur.closed + node_jpy.closed, [])
        self.assertEqual(node_eur.entered + node_jpy.entered, [])


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()
