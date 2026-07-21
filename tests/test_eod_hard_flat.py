"""Tests for the EOD hard-flat + daily-reset logic (Phase 1 / F2).

Builds an AxonDaemon shell via ``__new__`` so no MT5 connection or heavy
component construction is needed; only the pure timing/guard methods are
exercised, with ``_close_all_positions`` stubbed to record calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

from axonai.realtime.daemon import AxonDaemon


def _make_daemon(**cfg):
    d = AxonDaemon.__new__(AxonDaemon)
    d.config = {
        "eod_hard_flat_enabled": True,
        "eod_hard_flat_minutes_before": 10,
        **cfg,
    }
    d.mt5_symbol = "EURUSD"
    d._last_trading_day = None
    d._eod_flat_tradeday = None
    d._eod_flat_blocked = False
    d._sl_locked_out = False
    d._closed = []
    d._close_all_positions = lambda reason: (d._closed.append(reason) or 1)
    return d


def _jul(day, h, m):
    # July → US Eastern Daylight Time → ny_close = 18:00 UTC. Window = [17:50, 18:00).
    return datetime(2026, 7, day, h, m, tzinfo=timezone.utc)


class TestEodHardFlat(unittest.TestCase):
    def test_fires_inside_window_and_blocks(self):
        d = _make_daemon()
        d._check_daily_reset(_jul(22, 17, 52))   # seed trading day
        d._check_eod_hard_flat(_jul(22, 17, 52))
        self.assertEqual(len(d._closed), 1)
        self.assertTrue(d._eod_flat_blocked)

    def test_no_fire_before_window(self):
        d = _make_daemon()
        d._check_eod_hard_flat(_jul(22, 17, 40))
        self.assertEqual(d._closed, [])
        self.assertFalse(d._eod_flat_blocked)

    def test_no_fire_after_ny_close(self):
        d = _make_daemon()
        d._check_eod_hard_flat(_jul(22, 18, 5))
        self.assertEqual(d._closed, [])

    def test_fires_once_per_trading_day(self):
        d = _make_daemon()
        d._check_eod_hard_flat(_jul(22, 17, 52))
        d._check_eod_hard_flat(_jul(22, 17, 55))
        d._check_eod_hard_flat(_jul(22, 17, 59))
        self.assertEqual(len(d._closed), 1)      # only the first closes
        self.assertTrue(d._eod_flat_blocked)     # block stays on through the window

    def test_daily_reset_clears_block_after_ny_close(self):
        d = _make_daemon()
        d._check_daily_reset(_jul(22, 17, 52))
        d._check_eod_hard_flat(_jul(22, 17, 52))
        self.assertTrue(d._eod_flat_blocked)
        d._check_daily_reset(_jul(22, 18, 1))    # cross ny_close → trading day rolls
        self.assertFalse(d._eod_flat_blocked)

    def test_disabled_is_noop(self):
        d = _make_daemon(eod_hard_flat_enabled=False)
        d._check_eod_hard_flat(_jul(22, 17, 52))
        self.assertEqual(d._closed, [])

    def test_reflats_next_trading_day(self):
        d = _make_daemon()
        d._check_daily_reset(_jul(22, 17, 52))
        d._check_eod_hard_flat(_jul(22, 17, 52))
        d._check_daily_reset(_jul(22, 18, 1))    # roll into next trading day
        d._check_daily_reset(_jul(23, 17, 52))
        d._check_eod_hard_flat(_jul(23, 17, 52))
        self.assertEqual(len(d._closed), 2)


if __name__ == "__main__":
    unittest.main()
