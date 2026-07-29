"""Tests for the prop-firm compliance guard (overall drawdown + daily limit).

Defaults under test model a FundingPips 2-Step Standard $100k account:
10% overall drawdown (static, floor $90,000) and a 5% daily loss limit, with a
20% safety buffer so the tripwires sit at 8% ($92,000) and 4% ($4,000) — the
firm's real lines are never touched.

Critically also asserts the guard is INERT when disabled, so a non-prop account
(the live Eightcap terminal) is completely unaffected.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date

from axonai.realtime.risk_guard import RiskGuard


def _cfg(tmp, **over):
    cfg = {
        "prop_guard_enabled": True,
        "prop_max_drawdown_pct": 10.0,
        "prop_daily_loss_pct": 5.0,
        "prop_safety_buffer_pct": 20.0,
        "prop_max_drawdown_trailing": False,
        "prop_state_file": os.path.join(tmp, "prop_guard.json"),
        # Baseline must be explicit — the guard refuses to guess it (see
        # TestBaselineFailsClosed). Tests that probe seeding override this.
        "prop_initial_balance": 100_000.0,
    }
    cfg.update(over)
    return cfg


class _GuardCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _guard(self, **over):
        g = RiskGuard(_cfg(self.tmp, **over))
        # Keep the daily-PnL file inside the temp dir too (never touch reports/).
        g.risk_pnl_log_file = os.path.join(self.tmp, "daily_pnl.json")
        g.daily_pnl = {"date": g._today(), "start_equity": 0.0, "realized_pnl": 0.0}
        return g


class TestOverallDrawdown(_GuardCase):
    def test_floor_is_buffered_below_firm_line(self):
        g = self._guard()
        g.update_equity(100_000.0, 100_000.0)
        self.assertAlmostEqual(g.hard_floor(), 90_000.0)      # firm's real breach line
        self.assertAlmostEqual(g.drawdown_floor(), 92_000.0)  # we stop 2k early

    def test_not_halted_above_floor(self):
        # Day starts near current equity so the DAILY rule stays out of the way
        # and only the overall floor is under test.
        g = self._guard()
        g.update_equity(100_000.0, 100_000.0)
        g.daily_pnl = {"date": g._today(), "start_equity": 93_500.0, "realized_pnl": 0.0}
        halted, _ = g.is_halted(93_000.0)
        self.assertFalse(halted)

    def test_halts_at_buffered_floor(self):
        g = self._guard()
        g.update_equity(100_000.0, 100_000.0)
        g.daily_pnl = {"date": g._today(), "start_equity": 92_200.0, "realized_pnl": 0.0}
        halted, reason = g.is_halted(92_000.0)
        self.assertTrue(halted)
        self.assertIn("MAX DRAWDOWN", reason)

    def test_big_daily_loss_trips_daily_rule_first(self):
        # -7% in one day: well above the floor, but a clear daily breach.
        g = self._guard()
        g.update_equity(100_000.0, 100_000.0)
        halted, reason = g.is_halted(93_000.0)
        self.assertTrue(halted)
        self.assertIn("DAILY LOSS", reason)

    def test_multi_day_bleed_still_trips(self):
        # The whole point of the overall floor: each DAY's loss is small enough to
        # pass the daily check, but cumulatively the account is dead.
        g = self._guard()
        g.update_equity(100_000.0, 100_000.0)
        for eq in (98_000.0, 96_000.0, 94_000.0):
            g.daily_pnl = {"date": g._today(), "start_equity": eq, "realized_pnl": 0.0}
            self.assertFalse(g.is_halted(eq)[0], f"tripped too early at {eq}")
        g.daily_pnl = {"date": g._today(), "start_equity": 92_500.0, "realized_pnl": 0.0}
        self.assertTrue(g.is_halted(91_900.0)[0])

    def test_explicit_initial_balance_wins(self):
        g = self._guard(prop_initial_balance=100_000.0)
        g.update_equity(50_000.0, 50_000.0)    # wrong account state must not reseed
        self.assertAlmostEqual(g.hard_floor(), 90_000.0)

    def test_drawn_down_account_never_lowers_the_floor(self):
        # REGRESSION (critical): arming the guard while the account is already at
        # 94k must NOT re-baseline to 94k — that would put the floor at 84,600,
        # BELOW the firm's real 90,000 line, and the account would blow up while
        # the guard reported healthy.
        g = self._guard(prop_initial_balance=100_000.0)
        g.update_equity(94_000.0, 94_000.0)
        self.assertAlmostEqual(g.prop_state["initial_balance"], 100_000.0)
        self.assertAlmostEqual(g.hard_floor(), 90_000.0)
        g.daily_pnl = {"date": g._today(), "start_equity": 94_100.0, "realized_pnl": 0.0}
        self.assertTrue(g.is_halted(91_900.0)[0])   # trips above the firm's line

    def test_persisted_baseline_survives_restart(self):
        g1 = self._guard(prop_initial_balance=100_000.0)
        g1.update_equity(100_000.0, 100_000.0)
        g2 = RiskGuard(_cfg(self.tmp, prop_initial_balance=None))  # restart, no CLI arg
        self.assertEqual(g2.baseline_source, "persisted")
        self.assertAlmostEqual(g2.hard_floor(), 90_000.0)


class TestTrailingDrawdown(_GuardCase):
    def test_floor_ratchets_up_with_equity_and_never_back(self):
        g = self._guard(prop_max_drawdown_trailing=True, prop_max_drawdown_pct=5.0)
        g.update_equity(100_000.0, 100_000.0)
        self.assertAlmostEqual(g.hard_floor(), 95_000.0)
        g.update_equity(110_000.0, 110_000.0)          # new high → floor ratchets
        self.assertAlmostEqual(g.hard_floor(), 104_500.0)
        g.update_equity(105_000.0, 105_000.0)          # pull-back must NOT lower it
        self.assertAlmostEqual(g.hard_floor(), 104_500.0)


class TestDailyLimit(_GuardCase):
    def test_daily_trips_at_buffered_limit(self):
        g = self._guard()
        g.update_equity(100_000.0, 100_000.0)
        g.daily_pnl = {"date": g._today(), "start_equity": 100_000.0, "realized_pnl": 0.0}
        self.assertFalse(g.is_halted(96_500.0)[0])          # -3.5% → still trading
        halted, reason = g.is_halted(96_000.0)              # -4.0% → buffered trip
        self.assertTrue(halted)
        self.assertIn("DAILY LOSS", reason)

    def test_daily_limit_trips_before_firm_line(self):
        # Firm's line is 5% ($95,000); we must halt strictly above it.
        g = self._guard()
        g.update_equity(100_000.0, 100_000.0)
        g.daily_pnl = {"date": g._today(), "start_equity": 100_000.0, "realized_pnl": 0.0}
        self.assertTrue(g.is_halted(96_000.0)[0])
        self.assertGreater(96_000.0, 95_000.0)

    def test_legacy_small_account_limits_do_not_apply(self):
        # The legacy defaults (5% / $500) would trip at -$500 on a $100k account
        # and mask the real prop limits. Prop mode must supersede them.
        g = self._guard()
        g.config["risk_max_daily_loss_amount"] = 500.0
        g.max_daily_loss_amount = 500.0
        g.update_equity(100_000.0, 100_000.0)
        g.daily_pnl = {"date": g._today(), "start_equity": 100_000.0, "realized_pnl": 0.0}
        self.assertFalse(g.is_halted(99_000.0)[0])   # -$1,000: fine under prop rules


class TestBaselineFailsClosed(_GuardCase):
    """No known starting balance → refuse to trade rather than trade unprotected."""

    def test_halts_when_baseline_unknown(self):
        g = RiskGuard(_cfg(self.tmp, prop_initial_balance=None))
        g.risk_pnl_log_file = os.path.join(self.tmp, "daily_pnl.json")
        self.assertEqual(g.baseline_source, "")
        halted, reason = g.is_halted(100_000.0)
        self.assertTrue(halted)
        self.assertIn("no starting balance known", reason)

    def test_update_equity_never_seeds_baseline(self):
        g = RiskGuard(_cfg(self.tmp, prop_initial_balance=None))
        g.risk_pnl_log_file = os.path.join(self.tmp, "daily_pnl.json")
        g.update_equity(94_000.0, 94_000.0)
        self.assertFalse(g.prop_state.get("initial_balance"))
        self.assertTrue(g.is_halted(94_000.0)[0])   # still halted, still no guess

    def test_corrupt_state_file_does_not_rebaseline(self):
        # REGRESSION (critical): a lost/corrupt state file must not silently
        # re-seed the floor at the drawn-down balance.
        with open(os.path.join(self.tmp, "prop_guard.json"), "w") as f:
            f.write("{bad json")
        g = RiskGuard(_cfg(self.tmp, prop_initial_balance=None))
        g.risk_pnl_log_file = os.path.join(self.tmp, "daily_pnl.json")
        g.update_equity(94_000.0, 94_000.0)
        self.assertEqual(g.hard_floor(), 0.0)        # no floor invented
        self.assertTrue(g.is_halted(94_000.0)[0])    # fails closed instead


class TestDailyBaseline(_GuardCase):
    def test_uses_higher_of_equity_and_balance(self):
        # REGRESSION: first observation of the day arriving after a drop must not
        # become the denominator (that pushes the tripwire below the firm's line).
        g = self._guard()
        g.update_equity(96_500.0, 100_000.0)   # floating loss open at first sight
        self.assertAlmostEqual(g.daily_pnl["start_equity"], 100_000.0)
        self.assertTrue(g.is_halted(96_000.0)[0])   # -4% of the TRUE open

    def test_foreign_daily_baseline_is_rejected(self):
        # REGRESSION (critical): a daily file from a different (small) account
        # must not silently disable the funded account's daily limit.
        g = self._guard()
        g.daily_pnl = {"date": g._today(), "start_equity": 9_909.09, "realized_pnl": 0.0}
        halted, _ = g.is_halted(95_500.0)
        self.assertAlmostEqual(g.daily_pnl["start_equity"], 95_500.0)  # reseeded
        self.assertFalse(halted)

    def test_foreign_baseline_rejected_on_NON_prop_account_too(self):
        # REGRESSION (live blocker, observed 2026-07-30): reports/daily_pnl.json
        # held start_equity=100000 (the funded account) while the 10k Eightcap
        # lead read the same file. The sanity reseed was gated on prop_enabled, so
        # the small account trusted a 100k baseline → 90% "daily loss" → circuit
        # breaker tripped on the first tick and EVERY order was rejected all day.
        g = RiskGuard({"prop_guard_enabled": False})
        g.risk_pnl_log_file = os.path.join(self.tmp, "daily_pnl.json")
        g.daily_pnl = {"date": g._today(), "start_equity": 100_000.0, "realized_pnl": 0.0}
        halted, reason = g.is_halted(10_000.0)
        self.assertFalse(halted, f"foreign baseline still trips the breaker: {reason}")
        self.assertAlmostEqual(g.daily_pnl["start_equity"], 10_000.0)  # reseeded

    def test_non_prop_real_daily_loss_still_trips(self):
        # The reseed must not become a blanket amnesty: a plausible baseline (well
        # inside the 0.5x–2x window) must still enforce the daily limit.
        g = RiskGuard({"prop_guard_enabled": False, "risk_max_daily_drawdown_pct": 3.0})
        g.risk_pnl_log_file = os.path.join(self.tmp, "daily_pnl.json")
        g.daily_pnl = {"date": g._today(), "start_equity": 10_000.0, "realized_pnl": 0.0}
        halted, _ = g.is_halted(9_600.0)          # -4% of a believable baseline
        self.assertTrue(halted)
        self.assertAlmostEqual(g.daily_pnl["start_equity"], 10_000.0)  # NOT reseeded

    def test_prop_uses_separate_daily_file(self):
        # The funded process must not share reports/daily_pnl.json with a
        # non-prop process running from the same directory.
        prop = RiskGuard(_cfg(self.tmp))
        plain = RiskGuard({"prop_guard_enabled": False})
        self.assertNotEqual(prop.risk_pnl_log_file, plain.risk_pnl_log_file)
        self.assertEqual(plain.risk_pnl_log_file, "reports/daily_pnl.json")


class TestDisabledIsInert(_GuardCase):
    """A non-prop account must behave exactly as before."""

    def test_no_floor_and_no_state_file(self):
        cfg = _cfg(self.tmp, prop_guard_enabled=False)
        g = RiskGuard(cfg)
        g.risk_pnl_log_file = os.path.join(self.tmp, "daily_pnl.json")
        self.assertFalse(g.prop_enabled)
        self.assertEqual(g.drawdown_floor(), 0.0)
        self.assertEqual(g.hard_floor(), 0.0)
        g.update_equity(100_000.0, 100_000.0)
        # No prop state persisted for a non-prop account.
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "prop_guard.json")))

    def test_deep_loss_does_not_trip_prop_rules(self):
        cfg = _cfg(self.tmp, prop_guard_enabled=False)
        cfg["risk_max_daily_drawdown_pct"] = 50.0     # keep legacy checks out of the way
        cfg["risk_max_daily_loss_amount"] = 10 ** 9
        g = RiskGuard(cfg)
        g.risk_pnl_log_file = os.path.join(self.tmp, "daily_pnl.json")
        g.daily_pnl = {"date": str(date.today()), "start_equity": 100_000.0, "realized_pnl": 0.0}
        # 20% down would breach every prop rule — but the guard is off.
        self.assertFalse(g.is_halted(80_000.0)[0])

    def test_uses_local_date_when_disabled(self):
        cfg = _cfg(self.tmp, prop_guard_enabled=False)
        g = RiskGuard(cfg)
        self.assertEqual(g._today(), str(date.today()))


class TestConsistencyRule(_GuardCase):
    """The 45% payout gate: block NEW entries when today's day-PnL crosses
    the buffered ratio (36% with the default 20% buffer). Never flattens.
    """

    def _guard_pro(self, **over):
        # 2-Step Pro: 6% overall, 3% daily, 45% consistency, 20% buffer.
        pro = dict(prop_max_drawdown_pct=6.0, prop_daily_loss_pct=3.0,
                   prop_consistency_pct=45.0)
        pro.update(over)
        return self._guard(**pro)

    def test_no_profit_yet_never_blocks(self):
        g = self._guard_pro()
        g.update_equity(100_000.0, 100_000.0)  # flat account
        allowed, _ = g.entry_allowed(100_000.0, 100_000.0)
        self.assertTrue(allowed)

    def test_day_below_threshold_allows(self):
        # $5000 total profit, today +$1500 = 30% (< 36% buffered) → allowed.
        g = self._guard_pro()
        g.update_equity(105_000.0, 105_000.0)
        g.record_trade_result(1_500.0)
        allowed, reason = g.entry_allowed(105_000.0, 105_000.0)
        self.assertTrue(allowed, msg=reason)

    def test_day_at_threshold_blocks_new_entries(self):
        # $4000 total profit, today +$2000 = 50% → over 36% buffered → block.
        g = self._guard_pro()
        g.update_equity(104_000.0, 104_000.0)
        g.record_trade_result(2_000.0)
        allowed, reason = g.entry_allowed(104_000.0, 104_000.0)
        self.assertFalse(allowed)
        self.assertIn("CONSISTENCY", reason)

    def test_consistency_never_triggers_flatten_path(self):
        # is_halted must stay clean — daemon's flatten-on-breach must not fire
        # from a payout-gate trip (flattening would make the day worse).
        g = self._guard_pro()
        g.update_equity(104_000.0, 104_000.0)
        g.record_trade_result(2_000.0)  # 50% concentration
        halted, _ = g.is_halted(104_000.0)
        self.assertFalse(halted, "consistency trip must NOT halt/flatten")
        # But is_tripped (executor's gate) must be True.
        self.assertTrue(g.is_tripped)

    def test_best_day_persists_across_days(self):
        # Yesterday was concentrated; today is calm. The rule still blocks
        # because best_day / total > threshold.
        g = self._guard_pro()
        g.update_equity(104_000.0, 104_000.0)
        g.record_trade_result(2_000.0)                                   # yesterday's big day
        g.daily_pnl = {"date": "9999-01-01", "start_equity": 0.0, "realized_pnl": 0.0}
        g.update_equity(104_000.0, 104_000.0)                            # new day, no trades
        allowed, reason = g.entry_allowed(104_000.0, 104_000.0)
        self.assertFalse(allowed)
        self.assertIn("CONSISTENCY", reason)

    def test_disabled_when_pct_is_zero(self):
        g = self._guard_pro(prop_consistency_pct=0.0)
        g.update_equity(104_000.0, 104_000.0)
        g.record_trade_result(2_000.0)
        allowed, _ = g.entry_allowed(104_000.0, 104_000.0)
        self.assertTrue(allowed)

    def test_losing_day_never_sets_the_gate(self):
        # A -$2000 day should not become the "best day" and should not block.
        g = self._guard_pro()
        g.update_equity(103_000.0, 103_000.0)
        g.record_trade_result(-2_000.0)
        allowed, _ = g.entry_allowed(103_000.0, 103_000.0)
        self.assertTrue(allowed)
        self.assertEqual(g.prop_state.get("best_day_pnl", 0.0), 0.0)


class TestProfitTargetNotice(_GuardCase):
    def test_target_logged_once_and_does_not_halt(self):
        # 2-Step Pro: 6% target from 100k = 106k.
        g = self._guard(prop_profit_target_pct=6.0, prop_max_drawdown_pct=6.0,
                        prop_daily_loss_pct=3.0)
        g.update_equity(106_500.0, 106_500.0)
        # trigger via entry_allowed (which calls _check_profit_target)
        allowed, _ = g.entry_allowed(106_500.0, 106_500.0)
        self.assertTrue(allowed, "profit target is a notice, not a halt")
        self.assertTrue(g._target_hit_logged)
        # Second call must not re-log (idempotent).
        allowed2, _ = g.entry_allowed(107_000.0, 107_000.0)
        self.assertTrue(allowed2)


if __name__ == "__main__":
    unittest.main()
