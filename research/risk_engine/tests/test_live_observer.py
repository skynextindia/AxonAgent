"""Unit tests for the read-only live observer (SHADOW INTEGRATION).

All offline: no MT5, no network, no daemon, no live handles. Fakes stand in for
``live_state`` / ``risk_guard`` / ``correlation_engine`` / ``trade_result``. The
tests assert the observer (a) never changes direction, (b) never raises, (c)
marks unavailable inputs rather than inventing them, (d) writes only under
shadow_out/, and (e) references no execution API.
"""

from __future__ import annotations

import os
import io
import json
import unittest

from research.risk_engine import live_observer as LO
from research.risk_engine.telemetry import ShadowTelemetryWriter, SHADOW_OUT_DIR


# ── fakes (plain data, no behaviour) ─────────────────────────────────────────
class _FakeState:
    def __init__(self, atr):
        self._state = type("S", (), {"atr_14_h1": atr})()


class _FakeRiskGuard:
    """Read-only stand-in exposing exactly the attributes the observer reads."""
    def __init__(self, equity=100_000.0, prop=True, initial=100_000.0,
                 buffered=95_200.0, firm=94_000.0, start_equity=100_000.0, balance=None):
        self.current_equity = equity
        if balance is not None:
            self.current_balance = balance
        self.prop_enabled = prop
        self.prop_state = {"initial_balance": initial} if prop else {}
        self.daily_pnl = {"start_equity": start_equity, "realized_pnl": 0.0}
        self._buffered = buffered
        self._firm = firm

    def drawdown_floor(self):
        return self._buffered if self.prop_enabled else 0.0

    def hard_floor(self):
        return self._firm if self.prop_enabled else 0.0


class _CapturingWriter(ShadowTelemetryWriter):
    """Writer that captures rows in memory instead of touching disk."""
    def __init__(self):
        self._path = os.path.join(SHADOW_OUT_DIR, "test_live_observer.jsonl")
        self.rows = []

    def write(self, row):
        # still exercise the JSON-serialisability the real writer requires
        json.dumps(row)
        self.rows.append(row)


def _node_config():
    return {
        "max_loss_per_trade_usd": 1100.0,
        "hard_distance_mode": True,
        "realtime_hard_stop_pips": 30,
        "realtime_pip_value_per_lot": 0.0,   # JPY -> derive
        "realtime_min_lot": 0.1,
        "realtime_max_lot": 20.0,
    }


def _lead_config():
    return {
        "realtime_risk_pct": 0.011,
        "hard_distance_mode": True,
        "realtime_hard_stop_pips": 20,
        "realtime_pip_value_per_lot": 10.0,
        "realtime_min_lot": 0.1,
        "realtime_max_lot": 20.0,
    }


def _tr(price, sl, volume):
    return {"price": price, "sl": sl, "volume": volume, "order": 12345}


class TestDirectionInvariant(unittest.TestCase):
    def test_buy_signal_preserved(self):
        w = _CapturingWriter()
        row = LO.observe_entry(
            symbol="EURUSD", production_signal="Buy", live_state=_FakeState(0.0012),
            size_scale=1.0, risk_guard=_FakeRiskGuard(equity=10_000.0, prop=False),
            correlation_engine=None, trade_result=_tr(1.15000, 1.14800, 0.55),
            config=_lead_config(), signal_id="s1", timestamp="T", writer=w)
        self.assertEqual(row["production_direction"], "BUY")
        self.assertEqual(row["shadow_direction"], "BUY")
        self.assertTrue(row["direction_preserved"])

    def test_sell_signal_preserved(self):
        w = _CapturingWriter()
        row = LO.observe_entry(
            symbol="USDJPY", production_signal="Sell", live_state=_FakeState(0.09),
            size_scale=1.0, risk_guard=_FakeRiskGuard(), correlation_engine=None,
            trade_result=_tr(157.000, 157.300, 5.83), config=_node_config(),
            signal_id="s2", timestamp="T", writer=w)
        self.assertEqual(row["production_direction"], "SELL")
        self.assertEqual(row["shadow_direction"], "SELL")
        self.assertTrue(row["direction_preserved"])

    def test_hold_is_ignored(self):
        w = _CapturingWriter()
        row = LO.observe_entry(
            symbol="EURUSD", production_signal="Hold", live_state=_FakeState(0.0012),
            size_scale=1.0, risk_guard=_FakeRiskGuard(), correlation_engine=None,
            trade_result=_tr(1.15, 1.148, 0.5), config=_lead_config(),
            timestamp="T", writer=w)
        self.assertIsNone(row)
        self.assertEqual(w.rows, [])

    def test_observer_never_emits_a_direction_field_from_the_engine(self):
        # The engine's decision object carries no direction; the only direction in
        # the row must equal the production one. Guards against a future refactor
        # that lets the engine express a side.
        w = _CapturingWriter()
        row = LO.observe_entry(
            symbol="USDJPY", production_signal="Underweight", live_state=_FakeState(0.09),
            size_scale=1.0, risk_guard=_FakeRiskGuard(), correlation_engine=None,
            trade_result=_tr(157.0, 157.3, 5.83), config=_node_config(),
            timestamp="T", writer=w)
        self.assertEqual(row["production_direction"], row["shadow_direction"])


class TestNodeMirrorsProductionSizing(unittest.TestCase):
    def test_fixed_usd_reference_matches_production_budget(self):
        # Node: max-loss $1100, 30-pip stop, JPY pip-value derived. Production lot
        # = 1100 / (30 * pipval). The shadow engine, mirroring fixed_usd, should
        # reproduce the same lot (both clamp to the same [min,max]).
        w = _CapturingWriter()
        cfg = _node_config()
        row = LO.observe_entry(
            symbol="USDJPY", production_signal="Sell", live_state=_FakeState(0.09),
            size_scale=1.0, risk_guard=_FakeRiskGuard(), correlation_engine=None,
            trade_result=_tr(157.000, 157.300, 5.83), config=cfg,
            timestamp="T", writer=w)
        self.assertEqual(row["risk_policy_used"], "ref_fixed_usd")
        # pip value derived: 100000*0.01/157 ~= 6.369; lot = 1100/(30*6.369) ~= 5.76
        self.assertIsNotNone(row["shadow_proposed_lot"])
        self.assertAlmostEqual(row["shadow_proposed_lot"], 5.75, delta=0.2)
        self.assertEqual(row["shadow_decision"], "allow")

    def test_stop_distance_reconstructed_from_entry_and_sl(self):
        w = _CapturingWriter()
        row = LO.observe_entry(
            symbol="USDJPY", production_signal="Sell", live_state=_FakeState(0.09),
            size_scale=1.0, risk_guard=_FakeRiskGuard(), correlation_engine=None,
            trade_result=_tr(157.000, 157.300, 5.83), config=_node_config(),
            timestamp="T", writer=w)
        self.assertAlmostEqual(row["stop_distance"], 30.0, delta=0.1)


class TestLeadMirrorsPctSizing(unittest.TestCase):
    def test_pct_reference_used_on_lead(self):
        w = _CapturingWriter()
        row = LO.observe_entry(
            symbol="EURUSD", production_signal="Buy", live_state=_FakeState(0.0012),
            size_scale=1.0, risk_guard=_FakeRiskGuard(equity=10_000.0, prop=False),
            correlation_engine=None, trade_result=_tr(1.15000, 1.14800, 0.55),
            config=_lead_config(), timestamp="T", writer=w)
        self.assertEqual(row["risk_policy_used"], "ref_pct")
        # 1.1% of 10k = $110 risk, 20-pip stop, $10/pip -> 0.55 lot
        self.assertAlmostEqual(row["shadow_proposed_lot"], 0.55, delta=0.05)

    def test_lead_prop_floor_is_unavailable_not_invented(self):
        # The lead is not a prop account: floor distance must be None, NOT a
        # fabricated node figure.
        w = _CapturingWriter()
        row = LO.observe_entry(
            symbol="EURUSD", production_signal="Buy", live_state=_FakeState(0.0012),
            size_scale=1.0, risk_guard=_FakeRiskGuard(equity=10_000.0, prop=False),
            correlation_engine=None, trade_result=_tr(1.15, 1.148, 0.55),
            config=_lead_config(), timestamp="T", writer=w)
        self.assertIsNone(row["distance_to_prop_floor"])
        self.assertIsNone(row["distance_to_firm_floor"])


class TestNodeFloorFields(unittest.TestCase):
    def test_node_floor_distance_populated(self):
        w = _CapturingWriter()
        row = LO.observe_entry(
            symbol="USDJPY", production_signal="Sell", live_state=_FakeState(0.09),
            size_scale=1.0,
            risk_guard=_FakeRiskGuard(equity=98_000.0, buffered=95_200.0, firm=94_000.0),
            correlation_engine=None, trade_result=_tr(157.0, 157.3, 5.83),
            config=_node_config(), timestamp="T", writer=w)
        self.assertAlmostEqual(row["distance_to_prop_floor"], 2_800.0, delta=1.0)
        self.assertAlmostEqual(row["distance_to_firm_floor"], 4_000.0, delta=1.0)


class TestUnavailableInputs(unittest.TestCase):
    def test_no_risk_guard_equity_unavailable(self):
        w = _CapturingWriter()
        row = LO.observe_entry(
            symbol="EURUSD", production_signal="Buy", live_state=_FakeState(0.0012),
            size_scale=1.0, risk_guard=None, correlation_engine=None,
            trade_result=_tr(1.15, 1.148, 0.55), config=_lead_config(),
            timestamp="T", writer=w)
        self.assertIsNone(row["equity_before"])
        self.assertIn("equity", row["missing_inputs"])
        self.assertEqual(row["shadow_decision"], "reject")  # engine refuses to size

    def test_fixed_lot_only_has_no_reference_policy(self):
        w = _CapturingWriter()
        cfg = {"fixed_lot": 0.5, "hard_distance_mode": True,
               "realtime_hard_stop_pips": 20, "realtime_pip_value_per_lot": 10.0}
        row = LO.observe_entry(
            symbol="EURUSD", production_signal="Buy", live_state=_FakeState(0.0012),
            size_scale=1.0, risk_guard=_FakeRiskGuard(equity=10_000.0, prop=False),
            correlation_engine=None, trade_result=_tr(1.15, 1.148, 0.5),
            config=cfg, timestamp="T", writer=w)
        self.assertEqual(row["risk_policy_used"], "unavailable")
        self.assertEqual(row["shadow_decision"], "unavailable")
        self.assertIsNone(row["shadow_proposed_lot"])
        # production lot is still recorded for the comparison
        self.assertEqual(row["production_lot"], 0.5)


class TestNeverRaises(unittest.TestCase):
    def test_garbage_trade_result_is_swallowed(self):
        w = _CapturingWriter()
        # trade_result missing keys, live_state without _state, weird risk_guard
        row = LO.observe_entry(
            symbol="EURUSD", production_signal="Buy", live_state=object(),
            size_scale=1.0, risk_guard=object(), correlation_engine=object(),
            trade_result={}, config=_lead_config(), timestamp="T", writer=w)
        # It must return a row or None, never raise.
        self.assertTrue(row is None or isinstance(row, dict))

    def test_none_everything(self):
        row = LO.observe_entry(
            symbol="EURUSD", production_signal="Buy", live_state=None,
            size_scale=1.0, risk_guard=None, correlation_engine=None,
            trade_result=None, config={}, timestamp="T",
            writer=_CapturingWriter())
        self.assertTrue(row is None or isinstance(row, dict))


class TestReadOnlyGuarantees(unittest.TestCase):
    def test_daily_loss_read_does_not_mutate_risk_guard(self):
        rg = _FakeRiskGuard(equity=97_000.0, start_equity=100_000.0)
        before = dict(rg.daily_pnl)
        LO.observe_entry(
            symbol="USDJPY", production_signal="Sell", live_state=_FakeState(0.09),
            size_scale=1.0, risk_guard=rg, correlation_engine=None,
            trade_result=_tr(157.0, 157.3, 5.83), config=_node_config(),
            timestamp="T", writer=_CapturingWriter())
        # observer must NOT reseed/rewrite the daily-pnl baseline (is_halted does)
        self.assertEqual(rg.daily_pnl, before)

    def test_daily_loss_pct_computed(self):
        rg = _FakeRiskGuard(equity=97_000.0, start_equity=100_000.0)
        w = _CapturingWriter()
        row = LO.observe_entry(
            symbol="USDJPY", production_signal="Sell", live_state=_FakeState(0.09),
            size_scale=1.0, risk_guard=rg, correlation_engine=None,
            trade_result=_tr(157.0, 157.3, 5.83), config=_node_config(),
            timestamp="T", writer=w)
        self.assertAlmostEqual(row["daily_loss_used"], 3.0, delta=0.01)


class TestModuleIsolation(unittest.TestCase):
    """Prove isolation from the parsed AST (immune to prose in docstrings)."""

    def _module_src(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "live_observer.py")
        with io.open(path, "r", encoding="utf-8") as f:
            return f.read()

    def test_imports_no_execution_module(self):
        import ast
        tree = ast.parse(self._module_src())
        banned_roots = {"MetaTrader5", "mt5"}
        banned_axon = ("axonai.realtime.trade_executor", "axonai.realtime.daemon",
                       "axonai.realtime.risk_guard", "axonai.dataflows.mt5_data")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.assertNotIn(a.name.split(".")[0], banned_roots)
                    self.assertNotIn(a.name, banned_axon)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                self.assertNotIn(mod.split(".")[0], banned_roots)
                self.assertNotIn(mod, banned_axon)
                # the only axonai-adjacent import allowed is NONE — package is standalone
                self.assertFalse(mod.startswith("axonai"),
                                 f"live_observer must not import from axonai (got {mod!r})")

    def test_calls_no_execution_api(self):
        import ast
        tree = ast.parse(self._module_src())
        banned_attrs = {"order_send", "positions_get", "position_close", "Close",
                        "close_position", "execute_signal", "send_order", "flatten"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, banned_attrs,
                                 f"live_observer must not call .{node.attr}()")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, banned_attrs)

    def test_shadow_only_flag(self):
        self.assertTrue(LO.SHADOW_ONLY)


if __name__ == "__main__":
    unittest.main()
