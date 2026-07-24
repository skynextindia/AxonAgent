"""Tests for the revised end-of-day machine (F2, entry-cutoff + pre-rollover flat).

Behaviour under test:
  * 23:00 IST (17:30 UTC): stop opening NEW positions. Open trades are HELD and
    left to the engine's exits — NOT force-closed here.
  * ~5 min before the NY 5pm daily rollover (DST-aware: ny_close+3h ≈ 20:55 UTC
    ≈ 02:25 IST): force-flat ALL remaining positions, once per night.
  * 06:00 IST (00:30 UTC): the block lifts and the day resets; entries resume.

Builds an AxonDaemon shell via ``__new__`` so no MT5 connection is needed; only
the pure timing/guard logic is exercised, with ``_close_all_positions`` stubbed.
July dates → US Eastern Daylight Time → ny_close=18:00 UTC, rollover=21:00 UTC,
so the flatten window opens at 20:55 UTC.
"""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

from axonai.realtime.daemon import AxonDaemon


def _make_daemon(**cfg):
    d = AxonDaemon.__new__(AxonDaemon)
    d.config = {"eod_hard_flat_enabled": True, **cfg}
    d.mt5_symbol = "EURUSD"
    d._last_trading_day = None
    d._eod_flat_tradeday = None
    d._eod_flat_blocked = False
    d._sl_locked_out = False
    d._closed = []
    d._close_all_positions = lambda reason: (d._closed.append(reason) or 1)
    return d


def _utc(day, h, m):
    return datetime(2026, 7, day, h, m, tzinfo=timezone.utc)


class TestEodMachine(unittest.TestCase):
    def test_entry_blocked_at_2300_but_NOT_flattened(self):
        # The key change: at 23:00 IST we stop new entries but do NOT flatten.
        d = _make_daemon()
        d._check_eod_hard_flat(_utc(22, 17, 30))   # 23:00 IST
        self.assertTrue(d._eod_flat_blocked)        # no new entries
        self.assertEqual(d._closed, [])             # open trades HELD, not closed

    def test_not_blocked_just_before_2300(self):
        d = _make_daemon()
        d._check_eod_hard_flat(_utc(22, 17, 29))   # 22:59 IST
        self.assertFalse(d._eod_flat_blocked)
        self.assertEqual(d._closed, [])

    def test_held_through_hold_window_no_flatten(self):
        # Between 23:00 IST and the pre-rollover flat, positions stay open.
        d = _make_daemon()
        for t in [_utc(22, 18, 0), _utc(22, 20, 0), _utc(22, 20, 54)]:  # up to 20:54 (<20:55)
            d._check_eod_hard_flat(t)
        self.assertTrue(d._eod_flat_blocked)
        self.assertEqual(d._closed, [])            # nothing flattened yet

    def test_flatten_fires_5min_before_rollover(self):
        d = _make_daemon()
        d._check_eod_hard_flat(_utc(22, 20, 56))   # 02:26 IST, inside [20:55, resume)
        self.assertEqual(len(d._closed), 1)
        self.assertTrue(d._eod_flat_blocked)

    def test_full_night_sequence_flattens_once(self):
        d = _make_daemon()
        d._check_eod_hard_flat(_utc(22, 17, 30))   # cutoff: block, hold
        self.assertTrue(d._eod_flat_blocked); self.assertEqual(d._closed, [])
        d._check_eod_hard_flat(_utc(22, 20, 0))    # still holding
        self.assertEqual(d._closed, [])
        d._check_eod_hard_flat(_utc(22, 20, 56))   # pre-rollover flat
        d._check_eod_hard_flat(_utc(22, 23, 30))   # later same night — no re-flatten
        d._check_eod_hard_flat(_utc(23, 0, 0))     # past midnight UTC — no re-flatten
        self.assertEqual(len(d._closed), 1)
        self.assertTrue(d._eod_flat_blocked)
        d._check_eod_hard_flat(_utc(23, 0, 30))    # 06:00 IST — resume
        self.assertFalse(d._eod_flat_blocked)

    def test_resumes_at_0600_ist(self):
        d = _make_daemon()
        d._check_eod_hard_flat(_utc(23, 0, 29))    # 05:59 IST — still blocked
        self.assertTrue(d._eod_flat_blocked)
        d._check_eod_hard_flat(_utc(23, 0, 30))    # 06:00 IST — unblocked
        self.assertFalse(d._eod_flat_blocked)

    def test_reflattens_next_night(self):
        d = _make_daemon()
        d._check_eod_hard_flat(_utc(22, 20, 56))   # night 1 flat
        d._check_eod_hard_flat(_utc(23, 0, 30))    # resume
        d._check_eod_hard_flat(_utc(23, 20, 56))   # night 2 flat
        self.assertEqual(len(d._closed), 2)

    def test_restart_mid_flatten_window_reflattens(self):
        # Fresh shell (post-restart) inside the flatten window force-flats the
        # re-adopted positions on its first tick there.
        d = _make_daemon()
        d._check_eod_hard_flat(_utc(22, 22, 0))    # 03:30 IST, inside the flatten window
        self.assertEqual(len(d._closed), 1)
        self.assertTrue(d._eod_flat_blocked)

    def test_disabled_is_noop_and_unblocks(self):
        d = _make_daemon(eod_hard_flat_enabled=False)
        d._eod_flat_blocked = True
        d._check_eod_hard_flat(_utc(22, 20, 56))
        self.assertEqual(d._closed, [])
        self.assertFalse(d._eod_flat_blocked)


if __name__ == "__main__":
    unittest.main()
