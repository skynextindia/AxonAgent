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
    # Trading day now rolls at eod_resume_utc = 00:30 UTC (06:00 IST session reset).
    return datetime(2026, 7, day, h, m, tzinfo=timezone.utc)


class TestSlLockout(unittest.TestCase):
    def test_engages_on_losing_stop_loss(self):
        d = _make_daemon()
        d._maybe_engage_sl_lockout("Stop Loss (SL) Hit", pips=-8.0)
        self.assertTrue(d._sl_locked_out)

    def test_engages_on_losing_stop_out(self):
        d = _make_daemon()
        d._maybe_engage_sl_lockout("Stop Out (SO)", pips=-12.0)
        self.assertTrue(d._sl_locked_out)

    def test_does_not_engage_on_profitable_stop_label(self):
        # The classifier tags profitable *trailing* stops as "Stop Loss (SL) Hit"
        # too (broker "sl" comment). A winning stop must NOT lock the pair out —
        # this is the real-money bug this gate fixes.
        d = _make_daemon()
        d._maybe_engage_sl_lockout("Stop Loss (SL) Hit", pips=7.2)
        self.assertFalse(d._sl_locked_out)

    def test_does_not_engage_on_breakeven_stop(self):
        d = _make_daemon()
        d._maybe_engage_sl_lockout("Stop Loss (SL) Hit", pips=0.0)
        self.assertFalse(d._sl_locked_out)

    def test_does_not_engage_on_trailing_sl(self):
        # Trailing-SL exits are usually profitable and must NOT lock out.
        d = _make_daemon()
        d._maybe_engage_sl_lockout("Trailing SL Hit", pips=1.0)
        self.assertFalse(d._sl_locked_out)

    def test_does_not_engage_on_tp_or_manual(self):
        d = _make_daemon()
        for r in ("Take Profit (TP) Hit", "Manual Close / Unknown", "Closed (X)"):
            d._maybe_engage_sl_lockout(r, pips=-5.0)  # even a loss on these must not lock
            self.assertFalse(d._sl_locked_out, r)

    def test_daily_reset_clears_lockout(self):
        d = _make_daemon()
        d._check_daily_reset(_jul(22, 9, 0))                    # seed trading day (london)
        d._maybe_engage_sl_lockout("Stop Loss (SL) Hit", pips=-8.0)
        self.assertTrue(d._sl_locked_out)
        d._check_daily_reset(_jul(22, 12, 0))                   # same trading day → still locked
        self.assertTrue(d._sl_locked_out)
        d._check_daily_reset(_jul(22, 20, 0))                   # past old ny_close, still same day → STILL locked
        self.assertTrue(d._sl_locked_out)
        d._check_daily_reset(_jul(23, 0, 30))                   # 06:00 IST → new trading day → cleared
        self.assertFalse(d._sl_locked_out)

    def test_lockout_survives_until_roll(self):
        d = _make_daemon()
        d._check_daily_reset(_jul(22, 9, 0))
        d._maybe_engage_sl_lockout("Stop Loss (SL) Hit", pips=-8.0)
        # Survives all the way to the 06:00 IST reset — including the overnight
        # hold window past the old ny_close boundary and just before 00:30 UTC.
        for day, h, m in ((22, 16, 0), (22, 20, 0), (22, 23, 0), (23, 0, 0), (23, 0, 29)):
            d._check_daily_reset(_jul(day, h, m))
            self.assertTrue(d._sl_locked_out, f"cleared too early at {day} {h}:{m:02d}")


if __name__ == "__main__":
    unittest.main()
