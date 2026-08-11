"""Regression tests for the live chart-pattern breakout entry.

Locks the live detector to the EXACT offline geometry that produced the
OOS-validated +1R expectancy (shadow_patterns.csv). If these fail, the live
path has drifted from the validated signal.
"""
import unittest
from datetime import datetime, timedelta

from axonai.realtime.event_types import LiveCandle
from axonai.realtime.chart_patterns import _zigzag, _candidates, _first_break
from axonai.realtime.pattern_breakout_entry import PatternBreakoutDetector

T0 = datetime(2026, 8, 12, 0, 0)


def _mk_bars(closes):
    """Closed M15 LiveCandles with +/-1 pip high/low around each close."""
    bars = []
    for i, c in enumerate(closes):
        bars.append(LiveCandle(
            timeframe="M15", open_time=T0 + timedelta(minutes=15 * i),
            open=c, high=c + 0.0001, low=c - 0.0001, close=c,
            volume=10, is_closed=True,
        ))
    return bars


# 11 flat warm-up bars, then a textbook double top whose neckline (1.1000)
# breaks on the FINAL bar. Zigzag thr for EURUSD = 8p = 0.0008.
CLOSES = [1.1000] * 11 + [
    1.1004, 1.1008, 1.1012,   # rally to top1 (high 1.1013)
    1.1004, 1.1001,           # -8p confirms TOP pivot; bottom low 1.1000 = neckline
    1.1009, 1.1011,           # +8p confirms BOTTOM pivot; top2 (high 1.1012)
    1.1003,                   # -8p confirms TOP2 pivot
    1.0999,                   # first close below neckline -> BREAK on last bar
]


class TestPatternBreakoutDetector(unittest.TestCase):
    def setUp(self):
        self.det = PatternBreakoutDetector("EURUSD", {})
        self.bars = _mk_bars(CLOSES)

    def test_fires_double_top_on_break_bar(self):
        sig = self.det.on_m15_close(self.bars)
        self.assertIsNotNone(sig, "detector must fire on the neckline-break close")
        self.assertEqual(sig.pattern_type, "double_top")
        self.assertEqual(sig.direction, "SELL")
        self.assertAlmostEqual(sig.entry, 1.1000, places=5)   # neckline
        self.assertAlmostEqual(sig.sl, 1.1013, places=5)      # structural extreme
        self.assertAlmostEqual(sig.tp, 1.0987, places=5)      # 1R below neckline
        self.assertAlmostEqual(sig.risk_pips, 13.0, places=1)

    def test_bracket_is_exactly_1R(self):
        sig = self.det.on_m15_close(self.bars)
        self.assertIsNotNone(sig)
        self.assertAlmostEqual(abs(sig.tp - sig.entry), abs(sig.entry - sig.sl), places=9)

    def test_matches_offline_geometry(self):
        """Live signal must equal what the validated offline miner computes."""
        S = [[c.open, c.high, c.low, c.close,
              int((T0 + timedelta(minutes=15 * i)).timestamp())]
             for i, c in enumerate(self.bars)]
        piv = _zigzag(S, 8.0 * 0.0001)
        cands = list(_candidates(piv, S))
        self.assertEqual(len(cands), 1)
        typ, direction, down, neck, target, sl, frm = cands[0]
        self.assertEqual(typ, "double_top")
        b = _first_break(S, frm, neck, down)
        self.assertEqual(b, len(S) - 1, "offline break bar must be the newest bar")
        sig = self.det.on_m15_close(self.bars)
        self.assertIsNotNone(sig)
        self.assertAlmostEqual(sig.entry, neck, places=9)
        self.assertAlmostEqual(sig.sl, round(sl, 5), places=9)
        self.assertEqual(sig.direction, direction)

    def test_no_refire_after_break_bar_passes(self):
        self.assertIsNotNone(self.det.on_m15_close(self.bars))
        later = self.bars + _mk_bars([1.0996])
        # fix the appended bar's open_time to stay chronological
        later[-1].open_time = self.bars[-1].open_time + timedelta(minutes=15)
        self.assertIsNone(self.det.on_m15_close(later),
                          "break bar is no longer the newest bar -> must not fire")

    def test_dedup_same_break(self):
        self.assertIsNotNone(self.det.on_m15_close(self.bars))
        self.assertIsNone(self.det.on_m15_close(self.bars),
                          "identical break must be deduped")

    def test_gbp_excluded_by_default(self):
        det = PatternBreakoutDetector("GBPUSD", {})
        self.assertFalse(det.enabled)
        self.assertIsNone(det.on_m15_close(self.bars))

    def test_min_bars_guard(self):
        self.assertIsNone(self.det.on_m15_close(self.bars[:10]))

    def test_suffixed_broker_symbol_still_enabled(self):
        """A broker suffix (EURUSDm, EURUSD.et) must not silently disable the path."""
        self.assertTrue(PatternBreakoutDetector("EURUSDm", {}).enabled)
        self.assertTrue(PatternBreakoutDetector("USDJPY.et", {}).enabled)
        self.assertFalse(PatternBreakoutDetector("GBPUSDm", {}).enabled)

    def test_suffixed_symbol_fires(self):
        det = PatternBreakoutDetector("EURUSDm", {})
        sig = det.on_m15_close(self.bars)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.direction, "SELL")


class TestWeekendOverlap(unittest.TestCase):
    """Time-stop must count trading time, not weekend hours."""

    @classmethod
    def setUpClass(cls):
        from axonai.realtime.daemon import AxonDaemon
        cls.fn = staticmethod(AxonDaemon._weekend_overlap_seconds)

    def test_no_weekend_inside_week(self):
        # Tue 10:00 -> Wed 10:00 (2026-08-11/12)
        a = datetime(2026, 8, 11, 10, 0)
        b = datetime(2026, 8, 12, 10, 0)
        self.assertEqual(self.fn(a, b), 0.0)

    def test_full_weekend_counted(self):
        # Fri 15:00 -> Mon 15:00 spans the whole Fri21->Sun21 window (48h)
        a = datetime(2026, 8, 14, 15, 0)   # Friday
        b = datetime(2026, 8, 17, 15, 0)   # Monday
        self.assertEqual(self.fn(a, b), 48 * 3600.0)

    def test_partial_weekend(self):
        # Fri 15:00 -> Sat 09:00: overlap = Fri21 -> Sat09 = 12h
        a = datetime(2026, 8, 14, 15, 0)
        b = datetime(2026, 8, 15, 9, 0)
        self.assertEqual(self.fn(a, b), 12 * 3600.0)

    def test_friday_entry_effective_hold(self):
        # Fri 15:00 entry, checked Mon 08:00: wall clock 65h, trading time 17h.
        a = datetime(2026, 8, 14, 15, 0)
        b = datetime(2026, 8, 17, 8, 0)
        wall = (b - a).total_seconds()
        self.assertEqual(wall - self.fn(a, b), 17 * 3600.0)


if __name__ == "__main__":
    unittest.main()
