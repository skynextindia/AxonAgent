"""Offline unit tests for the direction/location forensics. No MT5, no network,
no production imports. Uses synthetic ForensicTrade records + the real journals
(read-only) for a smoke test.
"""

from __future__ import annotations

import os
import ast
import io
import unittest

from research.direction_location_forensics.loader import (
    ForensicTrade, _norm_symbol, _to_dir, _pip_size, load_all)
from research.direction_location_forensics import classify as C
from research.direction_location_forensics.classify import classify, forced_exit_kind


def _t(**kw):
    return ForensicTrade(**kw)


class TestLoaderHelpers(unittest.TestCase):
    def test_symbol_norm(self):
        self.assertEqual(_norm_symbol("EURUSD.i"), "EURUSD")
        self.assertEqual(_norm_symbol("EURUSD=X"), "EURUSD")
        self.assertEqual(_norm_symbol("USDJPY"), "USDJPY")

    def test_dir_norm(self):
        self.assertEqual(_to_dir("bearish_reversal".upper().split("_")[0]), "SELL")
        self.assertEqual(_to_dir("BUY"), "BUY")
        self.assertEqual(_to_dir("Sell"), "SELL")
        self.assertIsNone(_to_dir(None))

    def test_pip_size(self):
        self.assertEqual(_pip_size("USDJPY"), 0.01)
        self.assertEqual(_pip_size("EURUSD"), 0.0001)


class TestDirectionGrade(unittest.TestCase):
    def test_wrong_direction_dead_mfe(self):
        # MFE tiny vs stop -> never worked -> wrong direction
        v = classify(_t(direction="SELL", outcome="LOSS", pips=-20.0,
                        stop_pips=20.0, mfe_pips=1.0, mae_pips=20.0))
        self.assertEqual(v.direction_grade, "wrong")
        self.assertEqual(v.primary_bucket, "wrong_direction")
        self.assertEqual(v.confidence, "high")

    def test_right_direction_worked(self):
        v = classify(_t(direction="SELL", outcome="LOSS", pips=-5.0,
                        stop_pips=20.0, mfe_pips=14.0, mae_pips=20.0))
        self.assertEqual(v.direction_grade, "right")

    def test_proxy_direction_from_win(self):
        v = classify(_t(direction="BUY", outcome="WIN", pips=6.0, stop_pips=20.0))
        self.assertEqual(v.direction_grade, "right")
        self.assertEqual(v.confidence, "proxy")


class TestLocationGrade(unittest.TestCase):
    def test_bad_location_deep_mae(self):
        # direction worked (mfe ok) but deep adverse first -> bad location
        v = classify(_t(direction="SELL", outcome="LOSS", pips=-10.0,
                        stop_pips=20.0, mfe_pips=12.0, mae_pips=16.0))
        self.assertEqual(v.location_grade, "bad")
        self.assertEqual(v.primary_bucket, "bad_location")

    def test_thin_room_note(self):
        v = classify(_t(direction="SELL", outcome="LOSS", pips=-3.0, stop_pips=20.0,
                        sr_level_type="M15_SWING", sr_level_dist_pips=0.8))
        self.assertTrue(any("thin room" in n for n in v.notes))


class TestTimingGrade(unittest.TestCase):
    def test_faded_into_impulse(self):
        v = classify(_t(direction="SELL", outcome="LOSS", pips=-4.0, stop_pips=20.0,
                        displacement_ratio=0.72))
        self.assertEqual(v.timing_grade, "bad")

    def test_against_mtf_note(self):
        v = classify(_t(direction="SELL", outcome="LOSS", pips=-4.0, stop_pips=20.0,
                        mtf_alignment="BULLISH"))
        self.assertTrue(any("MTF" in n for n in v.notes))


class TestExitGrade(unittest.TestCase):
    def test_gave_back_winner(self):
        # winner-grade MFE, captured almost nothing -> gave_back -> bad_exit
        v = classify(_t(direction="SELL", outcome="LOSS", pips=-2.0,
                        stop_pips=20.0, mfe_pips=24.0, mae_pips=21.0))
        self.assertEqual(v.exit_grade, "gave_back")
        self.assertEqual(v.primary_bucket, "bad_exit")

    def test_good_capture_not_flagged(self):
        v = classify(_t(direction="SELL", outcome="WIN", pips=18.0,
                        stop_pips=20.0, mfe_pips=22.0, mae_pips=5.0))
        self.assertEqual(v.exit_grade, "ok")
        self.assertEqual(v.primary_bucket, "clean_win")


class TestRiskState(unittest.TestCase):
    def test_forced_exit_kinds(self):
        self.assertEqual(forced_exit_kind("Closed (Risk limit breach)"), "riskguard_breach")
        self.assertEqual(forced_exit_kind("Closed (EOD Flat (pre-rollover))"), "eod_flat")
        self.assertEqual(forced_exit_kind("Closed (Retest veto (veto))"), "retest_veto")
        self.assertIsNone(forced_exit_kind("Stop Loss (SL) Hit"))

    def test_winner_forced_out_is_risk_state(self):
        # winner-grade excursion, but forced out by a breach -> risk_state_distortion
        v = classify(_t(direction="SELL", outcome="LOSS", pips=-1.0, stop_pips=20.0,
                        mfe_pips=25.0, mae_pips=18.0, exit_reason="Closed (Risk limit breach)"))
        self.assertEqual(v.risk_state_effect, "riskguard_breach")
        self.assertEqual(v.primary_bucket, "risk_state_distortion")

    def test_proxy_loss_unattributed(self):
        # a plain SL loss with no MFE cannot be split -> kept separate
        v = classify(_t(direction="SELL", outcome="LOSS", pips=-20.0, stop_pips=20.0,
                        exit_reason="Stop Loss (SL) Hit"))
        self.assertEqual(v.primary_bucket, "loss_no_excursion_data")


class TestNeverInvent(unittest.TestCase):
    def test_missing_fields_stay_none(self):
        t = _t(direction="BUY", outcome="LOSS", pips=-5.0)
        self.assertIsNone(t.atr)
        self.assertIsNone(t.mfe_pips)
        self.assertIn("atr", t.missing)
        v = classify(t)
        self.assertIsNone(v.mfe_frac)


class TestRealJournalsSmoke(unittest.TestCase):
    def test_load_and_classify_real_history(self):
        data = load_all()
        self.assertIn("lead", data)
        self.assertIn("node", data)
        # lead history is large; node smaller. Both should classify without error.
        for acct, trades in data.items():
            for t in trades[:50]:
                v = classify(t)
                self.assertIn(v.primary_bucket, {
                    "clean_win", "wrong_direction", "bad_location", "bad_timing",
                    "bad_exit", "risk_state_distortion", "loss_no_excursion_data",
                    "unclassified_loss"})


class TestModuleIsolation(unittest.TestCase):
    def _src(self, name):
        d = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(d, name), "r", encoding="utf-8") as f:
            return f.read()

    def test_no_axonai_or_mt5_imports(self):
        for name in ("loader.py", "classify.py", "report.py", "run_forensics.py"):
            tree = ast.parse(self._src(name))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        self.assertNotEqual(a.name.split(".")[0], "MetaTrader5")
                        self.assertFalse(a.name.startswith("axonai"), f"{name}: imports {a.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    self.assertNotEqual(mod.split(".")[0], "MetaTrader5")
                    self.assertFalse(mod.startswith("axonai"), f"{name}: imports from {mod}")

    def test_no_execution_calls(self):
        banned = {"order_send", "positions_get", "position_close", "close_position",
                  "execute_signal", "send_order"}
        for name in ("loader.py", "classify.py", "report.py", "run_forensics.py"):
            tree = ast.parse(self._src(name))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, banned, f"{name}: calls .{node.attr}")


if __name__ == "__main__":
    unittest.main()
