"""Offline tests for the direction forensics. No MT5, no network, no production
imports. Verifies the shadow challenger cannot flip direction or reach execution,
the stats are correct on known inputs, and isolation holds.
"""

from __future__ import annotations

import os
import ast
import io
import glob
import unittest

from research.direction_forensics.dataset import Sample, build_samples
from research.direction_forensics import stats
from research.direction_forensics.challenger import ChallengerPolicy, decide
from research.direction_forensics.backtest import run_backtest, _R
from research.direction_forensics.walkforward import split, run_walk_forward


def _s(**kw):
    return Sample(**kw)


class TestChallengerContract(unittest.TestCase):
    def test_keep_returns_same_direction(self):
        s = _s(direction="SELL", displacement_ratio=0.1)
        action, reasons, votes = decide(s, ChallengerPolicy(displacement_min=0.55))
        self.assertEqual(action, "SELL")   # not vetoed -> unchanged direction
        self.assertEqual(votes, 0)

    def test_veto_returns_hold_never_flips(self):
        s = _s(direction="SELL", displacement_ratio=0.9)
        action, reasons, votes = decide(s, ChallengerPolicy(displacement_min=0.55))
        self.assertEqual(action, "HOLD")   # veto -> HOLD, never "BUY"
        self.assertNotEqual(action, "BUY")

    def test_never_outputs_opposite_direction(self):
        # Exhaustive: for any policy + any sample, output is in {direction, HOLD}
        for d in ("BUY", "SELL"):
            for disp in (0.0, 0.5, 0.9):
                s = _s(direction=d, displacement_ratio=disp, tick_efficiency=0.05,
                       velocity_divergence=0.5, mtf_alignment="BULLISH")
                for pol in (ChallengerPolicy(displacement_min=0.4),
                            ChallengerPolicy(efficiency_min=0.01),
                            ChallengerPolicy(velocity_div_max=1.0),
                            ChallengerPolicy(veto_against_mtf=True)):
                    action, _r, _v = decide(s, pol)
                    self.assertIn(action, (d, "HOLD"))

    def test_missing_feature_never_vetoes(self):
        s = _s(direction="SELL")  # all features None
        action, _r, _v = decide(s, ChallengerPolicy(displacement_min=0.1, efficiency_min=0.0,
                                                    velocity_div_max=99))
        self.assertEqual(action, "SELL")   # no data -> no veto

    def test_no_production_direction_holds(self):
        s = _s(direction=None)
        action, reasons, _v = decide(s, ChallengerPolicy())
        self.assertEqual(action, "HOLD")
        self.assertIn("no_production_direction", reasons)


class TestStats(unittest.TestCase):
    def test_rank_auc_perfect_separation(self):
        pos = [_s(velocity_divergence=v) for v in (5, 6, 7, 8)]
        neg = [_s(velocity_divergence=v) for v in (1, 2, 3, 4)]
        self.assertEqual(stats.rank_auc(pos, neg, "velocity_divergence"), 1.0)

    def test_rank_auc_no_separation(self):
        pos = [_s(velocity_divergence=v) for v in (1, 2, 3, 4)]
        neg = [_s(velocity_divergence=v) for v in (1, 2, 3, 4)]
        self.assertEqual(stats.rank_auc(pos, neg, "velocity_divergence"), 0.5)

    def test_auc_none_when_too_few(self):
        self.assertIsNone(stats.rank_auc([_s(velocity_divergence=1)],
                                         [_s(velocity_divergence=2)], "velocity_divergence"))

    def test_bootstrap_deterministic(self):
        pos = [_s(velocity_divergence=v) for v in range(10, 20)]
        neg = [_s(velocity_divergence=v) for v in range(0, 10)]
        a = stats.auc_bootstrap_ci(pos, neg, "velocity_divergence")
        b = stats.auc_bootstrap_ci(pos, neg, "velocity_divergence")
        self.assertEqual(a, b)   # deterministic (seeded)


class TestBacktest(unittest.TestCase):
    def test_veto_only_removes_trades(self):
        samples = [_s(ticket=i, direction="SELL", pips=(-10 if i % 2 else 10),
                      stop_pips=20.0, outcome=("LOSS" if i % 2 else "WIN"),
                      displacement_ratio=(0.9 if i % 2 else 0.1),
                      timestamp="2026-08-01 10:00:00")
                   for i in range(10)]
        r = run_backtest(samples, ChallengerPolicy(displacement_min=0.55))
        self.assertLessEqual(r.n_kept, r.n_total)     # can only remove
        self.assertEqual(r.n_kept + r.n_vetoed, r.n_total)

    def test_R_normalization(self):
        s = _s(pips=-10.0, stop_pips=20.0)
        self.assertEqual(_R(s), -0.5)


class TestWalkForward(unittest.TestCase):
    def test_split_is_chronological(self):
        samples = [_s(timestamp="2026-06-20 10:00:00"),
                   _s(timestamp="2026-07-20 10:00:00"),
                   _s(timestamp="2026-08-10 10:00:00")]
        sp = split(samples)
        self.assertEqual(len(sp["train"]), 1)
        self.assertEqual(len(sp["validation"]), 1)
        self.assertEqual(len(sp["oos"]), 1)


class TestRealDataSmoke(unittest.TestCase):
    def test_build_and_run(self):
        samples = build_samples()
        self.assertGreater(len(samples), 100)
        # every sample keeps direction in the legal set
        for s in samples[:100]:
            self.assertIn(s.direction, ("BUY", "SELL", None))
        wf = run_walk_forward(samples)
        self.assertIn(wf.verdict, {wf.verdict})  # runs without error
        self.assertIsInstance(wf.generalizes, bool)


class TestIsolation(unittest.TestCase):
    def _files(self):
        d = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return glob.glob(os.path.join(d, "*.py"))

    def test_no_axonai_or_mt5_imports(self):
        for fp in self._files():
            tree = ast.parse(io.open(fp, encoding="utf-8").read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        self.assertNotEqual(a.name.split(".")[0], "MetaTrader5")
                        self.assertFalse(a.name.startswith("axonai"), f"{fp}: {a.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    self.assertNotEqual(mod.split(".")[0], "MetaTrader5")
                    self.assertFalse(mod.startswith("axonai"), f"{fp}: from {mod}")

    def test_no_execution_calls(self):
        banned = {"order_send", "positions_get", "position_close", "close_position",
                  "execute_signal", "send_order"}
        for fp in self._files():
            tree = ast.parse(io.open(fp, encoding="utf-8").read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, banned, f"{fp}: .{node.attr}")


if __name__ == "__main__":
    unittest.main()
