"""Unit tests for per-pair calibration (resolve_symbol_config).

Pure-logic tests with no MetaTrader5 / network dependencies, so they run
everywhere. Executor-level sizing (which needs the MT5 mock) lives in
test_trade_execution.py.
"""

from __future__ import annotations

import unittest

from axonai.default_config import (
    DEFAULT_CONFIG,
    SYMBOL_CALIBRATION,
    resolve_symbol_config,
)


class TestResolveSymbolConfig(unittest.TestCase):
    def test_eurusd_preserves_legacy_behavior(self):
        # Must match the old hardcoded path exactly (magic 123457, 0.0001 pip,
        # 2xATR SL/TP, $10 pip value, 1% risk) so EURUSD behavior is unchanged.
        c = resolve_symbol_config(DEFAULT_CONFIG, "EURUSD")
        self.assertEqual(c["realtime_magic_number"], 123457)
        self.assertEqual(c["pip_size"], 0.0001)
        self.assertEqual(c["sl_atr_mult"], 2.0)
        self.assertEqual(c["tp_atr_mult"], 2.0)
        self.assertEqual(c["min_stop_pips"], 16.0)
        self.assertEqual(c["realtime_pip_value_per_lot"], 10.0)
        self.assertEqual(c["realtime_risk_pct"], 0.01)

    def test_usdjpy_distinct_and_dynamic(self):
        c = resolve_symbol_config(DEFAULT_CONFIG, "USDJPY")
        self.assertEqual(c["realtime_magic_number"], 123458)
        self.assertEqual(c["pip_size"], 0.01)
        # USD-base pair: pip value is price-dependent → computed at order time.
        self.assertIsNone(c["realtime_pip_value_per_lot"])

    def test_magics_distinct_across_pairs(self):
        e = resolve_symbol_config(DEFAULT_CONFIG, "EURUSD")["realtime_magic_number"]
        j = resolve_symbol_config(DEFAULT_CONFIG, "USDJPY")["realtime_magic_number"]
        self.assertNotEqual(e, j)

    def test_broker_suffix_and_yf_forms_canonicalize(self):
        for form in ("EURUSDm", "EURUSD.i", "EURUSD=X", "eurusd"):
            self.assertEqual(
                resolve_symbol_config(DEFAULT_CONFIG, form)["realtime_magic_number"],
                123457,
                form,
            )

    def test_risk_pct_dead_key_repaired(self):
        # Executor reads realtime_risk_pct; the flat config only had trade_risk_pct.
        base = dict(DEFAULT_CONFIG, trade_risk_pct=0.02)
        base.pop("realtime_risk_pct", None)
        # EURUSD spec pins 0.01.
        self.assertEqual(resolve_symbol_config(base, "EURUSD")["realtime_risk_pct"], 0.01)
        # An unlisted pair inherits trade_risk_pct through the repaired key.
        self.assertEqual(resolve_symbol_config(base, "GBPUSD")["realtime_risk_pct"], 0.02)

    def test_unlisted_pip_value_is_quote_aware(self):
        # USD-quote (XXXUSD) → $10 constant; USD-base / cross → None (dynamic).
        self.assertEqual(resolve_symbol_config(DEFAULT_CONFIG, "GBPUSD")["realtime_pip_value_per_lot"], 10.0)
        self.assertEqual(resolve_symbol_config(DEFAULT_CONFIG, "AUDUSD")["realtime_pip_value_per_lot"], 10.0)
        self.assertIsNone(resolve_symbol_config(DEFAULT_CONFIG, "USDCHF")["realtime_pip_value_per_lot"])
        self.assertIsNone(resolve_symbol_config(DEFAULT_CONFIG, "EURJPY")["realtime_pip_value_per_lot"])

    def test_unlisted_pip_size_auto_derived(self):
        self.assertEqual(resolve_symbol_config(DEFAULT_CONFIG, "EURJPY")["pip_size"], 0.01)
        self.assertEqual(resolve_symbol_config(DEFAULT_CONFIG, "GBPUSD")["pip_size"], 0.0001)

    def test_does_not_mutate_base(self):
        base = dict(DEFAULT_CONFIG)
        snapshot = dict(base)
        resolve_symbol_config(base, "USDJPY")
        self.assertEqual(base, snapshot)

    def test_calibration_table_magics_distinct(self):
        magics = [s["magic_number"] for s in SYMBOL_CALIBRATION.values()]
        self.assertEqual(len(magics), len(set(magics)))


if __name__ == "__main__":
    unittest.main()
