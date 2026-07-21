"""Tests for the per-pair SL lockout + daily reset (Phase 2 / F4).

Uses an AxonDaemon shell (``__new__``) so no MT5 connection is required; the
lockout decision and the trading-day reset are pure in-memory logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

from axonai.realtime.daemon import AxonDaemon


def _make_daemon():
    d = AxonDaemon.__new__(AxonDaemon)
    d.config = {}
    d.mt5_symbol = "USDJPY"
    d._last_trading_day = None
    d._eod_flat_tradeday = None
    d._eod_flat_blocked = False
    d._sl_locked_out = False
    return d


def _jul(day, h, m):
    # July → US-EDT → trading day rolls at ny_close = 18:00 UTC.
    return datetime(2026, 7, day, h, m, tzinfo=timezone.utc)


class TestSlLockout(unittest.TestCase):
    def test_engages_on_stop_loss(self):
        d = _make_daemon()
        d._maybe_engage_sl_lockout("Stop Loss (SL) Hit")
        self.assertTrue(d._sl_locked_out)

    def test_engages_on_stop_out(self):
        d = _make_daemon()
        d._maybe_engage_sl_lockout("Stop Out (SO)")
        self.assertTrue(d._sl_locked_out)

    def test_does_not_engage_on_trailing_sl(self):
        # Trailing-SL exits are usually profitable and must NOT lock out.
        d = _make_daemon()
        d._maybe_engage_sl_lockout("Trailing SL Hit")
        self.assertFalse(d._sl_locked_out)

    def test_does_not_engage_on_tp_or_manual(self):
        d = _make_daemon()
        for r in ("Take Profit (TP) Hit", "Manual Close / Unknown", "Closed (X)"):
            d._maybe_engage_sl_lockout(r)
            self.assertFalse(d._sl_locked_out, r)

    def test_daily_reset_clears_lockout(self):
        d = _make_daemon()
        d._check_daily_reset(_jul(22, 9, 0))       # seed trading day (london)
        d._maybe_engage_sl_lockout("Stop Loss (SL) Hit")
        self.assertTrue(d._sl_locked_out)
        d._check_daily_reset(_jul(22, 12, 0))      # same trading day → still locked
        self.assertTrue(d._sl_locked_out)
        d._check_daily_reset(_jul(22, 18, 30))     # rolled past ny_close → cleared
        self.assertFalse(d._sl_locked_out)

    def test_lockout_survives_until_roll(self):
        d = _make_daemon()
        d._check_daily_reset(_jul(22, 9, 0))
        d._maybe_engage_sl_lockout("Stop Loss (SL) Hit")
        for h in (10, 13, 16, 17):
            d._check_daily_reset(_jul(22, h, 0))
            self.assertTrue(d._sl_locked_out, f"cleared too early at {h}:00")


if __name__ == "__main__":
    unittest.main()
