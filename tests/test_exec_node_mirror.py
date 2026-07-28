"""Tests for the A→B order mirror + execution node (decision routing; no MT5).

Exercises the pure routing/plumbing with ``AxonDaemon.__new__`` shells and fake
executors/daemons — the real ``mt5.order_send`` path is never touched. Covers:
  * inject_signal() routing an entry to the engine's executor + position tracking
  * inject_close() flattening via _close_all_positions
  * _mirror_send() being a no-op when disabled and canonicalising the symbol
  * the execution-node magic-offset + max-lot overrides
  * ExecNodeServer._dispatch routing enter/close/ping and rejecting unknown pairs
"""

from __future__ import annotations

import types
import unittest

from axonai.realtime.daemon import AxonDaemon
from axonai.realtime.exec_node import ExecNodeServer


class _FakeExecutor:
    def __init__(self, magic=123457, result=None):
        self.magic = magic
        self.result = result
        self.calls = []

    def execute_signal(self, symbol, signal, live_state, size_scale=1.0):
        self.calls.append((symbol, signal, size_scale))
        return self.result


def _shell(mt5_symbol="EURUSD.i", executor=None, mirror=None, config=None):
    d = AxonDaemon.__new__(AxonDaemon)
    d.mt5_symbol = mt5_symbol
    d.config = config or {}
    d.trade_executor = executor or _FakeExecutor()
    d.trade_executor_opt = d.trade_executor
    d.live_state = types.SimpleNamespace(_state=types.SimpleNamespace(atr_14_h1=0.0012))
    d.correlation_engine = None
    d.mirror_client = mirror
    d._tracked_positions = set()
    d._active_trade_initial_sl = {}
    d._active_trade_system = {}
    d._active_trade_atr = {}
    d._active_trade_peak_price = {}
    return d


class TestInjectSignal(unittest.TestCase):
    def test_routes_entry_to_executor_and_tracks(self):
        ex = _FakeExecutor(result={"order": 555, "sl": 1.09, "price": 1.10, "volume": 5.0})
        d = _shell(executor=ex)
        res = d.inject_signal("Buy", 0.5, source="mirror")
        self.assertEqual(res["order"], 555)
        self.assertEqual(ex.calls, [("EURUSD.i", "Buy", 0.5)])   # own symbol + scale forwarded
        self.assertIn(555, d._tracked_positions)                 # picked up by native management
        self.assertEqual(d._active_trade_system[555], "mirror")

    def test_ignores_non_entry_signal(self):
        ex = _FakeExecutor(result={"order": 1})
        d = _shell(executor=ex)
        self.assertIsNone(d.inject_signal("Hold"))
        self.assertEqual(ex.calls, [])

    def test_no_fill_does_not_track(self):
        ex = _FakeExecutor(result=None)
        d = _shell(executor=ex)
        self.assertIsNone(d.inject_signal("Sell"))
        self.assertEqual(len(d._tracked_positions), 0)


class TestInjectClose(unittest.TestCase):
    def test_calls_close_all_positions(self):
        d = _shell()
        seen = []
        d._close_all_positions = lambda reason: (seen.append(reason) or 3)
        self.assertEqual(d.inject_close("EOD flat"), 3)
        self.assertEqual(seen, ["EOD flat"])


class TestMirrorSend(unittest.TestCase):
    def test_noop_when_disabled(self):
        d = _shell(mirror=None)
        d._mirror_send({"cmd": "enter", "signal": "Buy"})  # must not raise

    def test_forwards_with_canonical_symbol(self):
        class FakeMirror:
            def __init__(self):
                self.sent = []

            def send(self, p):
                self.sent.append(p)

        m = FakeMirror()
        d = _shell(mt5_symbol="EURUSD.i", mirror=m)
        d._mirror_send({"cmd": "enter", "signal": "Buy", "size_scale": 1.0})
        self.assertEqual(len(m.sent), 1)
        self.assertEqual(m.sent[0]["symbol"], "EURUSD")   # broker suffix stripped for the wire
        self.assertEqual(m.sent[0]["cmd"], "enter")


class TestExecNodeOverrides(unittest.TestCase):
    def test_magic_offset_and_maxlot(self):
        d = AxonDaemon.__new__(AxonDaemon)
        d.mt5_symbol = "EURUSD"
        d.config = {"realtime_max_lot": 2.0, "realtime_magic_number": 123457,
                    "exec_node_max_lot": 5.0, "exec_node_magic_offset": 500000}
        d.trade_executor_opt = _FakeExecutor(magic=123457)
        d._apply_exec_node_overrides()
        self.assertEqual(d.config["realtime_max_lot"], 5.0)          # conservative cap applied
        self.assertEqual(d.trade_executor_opt.magic, 623457)         # distinct from lead
        self.assertEqual(d.config["realtime_magic_number"], 623457)


class _FakeDaemon:
    mt5_symbol = "EURUSD.i"

    def __init__(self):
        self.enter_calls = []
        self.close_reason = None

    def inject_signal(self, signal, size_scale, source="mirror"):
        self.enter_calls.append((signal, size_scale, source))
        return {"order": 777, "volume": 5.0}

    def inject_close(self, reason):
        self.close_reason = reason
        return 2


class TestExecNodeServerDispatch(unittest.TestCase):
    def _server(self, fake):
        return ExecNodeServer({"EURUSD.i": fake}, port=0)  # re-keys to canonical EURUSD

    def test_dispatch_enter(self):
        fake = _FakeDaemon()
        ack = self._server(fake)._dispatch(
            {"cmd": "enter", "symbol": "EURUSD", "signal": "Buy", "size_scale": 0.5})
        self.assertTrue(ack["ok"])
        self.assertEqual(ack["ticket"], 777)
        self.assertEqual(fake.enter_calls, [("Buy", 0.5, "mirror")])

    def test_dispatch_close(self):
        fake = _FakeDaemon()
        ack = self._server(fake)._dispatch({"cmd": "close", "symbol": "EURUSD", "reason": "SL"})
        self.assertTrue(ack["ok"])
        self.assertEqual(ack["closed"], 2)
        self.assertEqual(fake.close_reason, "SL")

    def test_dispatch_unknown_symbol(self):
        ack = self._server(_FakeDaemon())._dispatch(
            {"cmd": "enter", "symbol": "GBPUSD", "signal": "Buy"})
        self.assertFalse(ack["ok"])

    def test_dispatch_ping(self):
        ack = self._server(_FakeDaemon())._dispatch({"cmd": "ping"})
        self.assertEqual(ack["cmd"], "pong")


if __name__ == "__main__":
    unittest.main()
