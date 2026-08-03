"""Unit tests for the in-process MT5 auto-reconnect (self-heal a dropped link).

Motivating incident (2026-08-03): the FundingPips terminal auto-updated/restarted
at 14:14; the node daemon's process stayed alive but its MT5 connection went dead
with no reconnect, so every routed order was dropped for ~7.5h while the tick loop
silently ran on synthetic ticks. is_mt5_connected() catches the stale-_initialized
case, and mt5_reconnect() re-initializes against the SAME terminal, throttled and
serialized so the per-pair threads cooperate.
"""
import time
import unittest

import axonai.dataflows.mt5_data as md


class _FakeInfo:
    def __init__(self, path="C:\\Fake", connected=True):
        self.path = path
        self.company = "Fake Broker"
        self.connected = connected


class _FakeMT5:
    """Minimal stand-in for the MetaTrader5 module used by the reconnect path."""
    def __init__(self, connected=True):
        self._connected = connected
        self.shutdown_calls = 0

    def terminal_info(self):
        return _FakeInfo(connected=True) if self._connected else None

    def shutdown(self):
        self.shutdown_calls += 1
        self._connected = False


class MT5ReconnectTest(unittest.TestCase):
    def setUp(self):
        # snapshot the module globals we mutate, restore in tearDown
        self._snap = (md._mt5, md._initialized, md._terminal_path,
                      md._last_reconnect_ts, md._reconnect_min_interval)
        md._reconnect_min_interval = 30.0
        md._last_reconnect_ts = 0.0
        md._terminal_path = "C:\\Fake\\terminal64.exe"

    def tearDown(self):
        (md._mt5, md._initialized, md._terminal_path,
         md._last_reconnect_ts, md._reconnect_min_interval) = self._snap

    # ── is_mt5_connected ──────────────────────────────────────────────────────
    def test_not_connected_when_uninitialized(self):
        md._initialized = False
        md._mt5 = _FakeMT5(connected=True)
        self.assertFalse(md.is_mt5_connected())

    def test_not_connected_when_terminal_info_none(self):
        # the stale-flag case: initialized True, but terminal restarted → None
        md._initialized = True
        md._mt5 = _FakeMT5(connected=False)
        self.assertFalse(md.is_mt5_connected())

    def test_connected_when_terminal_live(self):
        md._initialized = True
        md._mt5 = _FakeMT5(connected=True)
        self.assertTrue(md.is_mt5_connected())

    # ── mt5_reconnect ─────────────────────────────────────────────────────────
    def test_reconnect_noop_when_already_connected(self):
        md._initialized = True
        fake = _FakeMT5(connected=True)
        md._mt5 = fake
        self.assertTrue(md.mt5_reconnect())
        self.assertEqual(fake.shutdown_calls, 0)  # never tore down a live link

    def test_reconnect_reinitializes_when_down(self):
        md._initialized = True
        fake = _FakeMT5(connected=False)
        md._mt5 = fake
        calls = {}

        def _stub_init(terminal_path=None, **kw):
            calls["path"] = terminal_path
            fake._connected = True      # terminal came back
            md._initialized = True
            return True

        _orig = md.mt5_initialize
        md.mt5_initialize = _stub_init
        try:
            self.assertTrue(md.mt5_reconnect())
        finally:
            md.mt5_initialize = _orig
        self.assertEqual(fake.shutdown_calls, 1)              # tore down the dead link
        self.assertEqual(calls["path"], "C:\\Fake\\terminal64.exe")  # SAME terminal

    def test_reconnect_throttled_within_interval(self):
        md._initialized = True
        md._mt5 = _FakeMT5(connected=False)
        md._reconnect_min_interval = 999.0
        md._last_reconnect_ts = time.monotonic()  # just attempted
        init_calls = []
        _orig = md.mt5_initialize
        md.mt5_initialize = lambda **k: (init_calls.append(1), True)[1]
        try:
            self.assertFalse(md.mt5_reconnect())   # throttled
        finally:
            md.mt5_initialize = _orig
        self.assertEqual(init_calls, [])           # did NOT re-init while throttled

    def test_reconnect_reports_false_when_init_fails(self):
        md._initialized = True
        md._mt5 = _FakeMT5(connected=False)
        _orig = md.mt5_initialize
        md.mt5_initialize = lambda **k: False       # terminal still not back
        try:
            self.assertFalse(md.mt5_reconnect())
        finally:
            md.mt5_initialize = _orig

    def test_reconnect_swallows_wrong_terminal_guard(self):
        # mt5_initialize raises (e.g. wrong-terminal guard) → reconnect returns
        # False instead of propagating, so the tick loop keeps running.
        md._initialized = True
        md._mt5 = _FakeMT5(connected=False)

        def _boom(**k):
            raise RuntimeError("wrong terminal")

        _orig = md.mt5_initialize
        md.mt5_initialize = _boom
        try:
            self.assertFalse(md.mt5_reconnect())
        finally:
            md.mt5_initialize = _orig


if __name__ == "__main__":
    unittest.main()
