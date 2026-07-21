"""Execution-quality telemetry on TradeRecord (spread, slippage, protect-floor ref).

Verifies record_entry captures fill quality and the exit-floor reference so trades
are diagnosable offline. Backward compat: record_entry still works without the new
optional args.
"""
import json
import types

from axonai.realtime.trade_analytics import TradeAnalytics


def _snap(vol_pips=0.9, tick_eff=0.5):
    vel = types.SimpleNamespace(
        vol_pips=vol_pips, percentile=70.0, tick_efficiency=tick_eff,
        decay_ratio=0.3, is_unusual=False, z_score=0.0,
    )
    return types.SimpleNamespace(
        velocity=vel, displacement=None, mtf=None, regime=None,
        liquidity=None, location_context=None, entry_decision=None,
        trade_health=None, trade_state=None,
    )


def _read_last(path):
    with open(path, encoding="utf-8") as f:
        return json.loads([ln for ln in f if ln.strip()][-1])


def test_slippage_signed_adverse_buy(tmp_path):
    ta = TradeAnalytics(log_dir=str(tmp_path))
    ta.record_entry(1, "EURUSD", "BUY", 1.10000, 1.09920, 1.10160, _snap(),
                    spread_pips=0.8, fill_price=1.10003)
    ta.record_exit(1, 1.10050, 5.0, "Take profit", _snap())
    rec = _read_last(ta._log_file)
    assert rec["entry_requested_price"] == 1.10000
    assert rec["entry_fill_price"] == 1.10003
    assert rec["entry_slippage_pips"] == 0.3   # filled 0.3p worse than signal
    assert rec["entry_spread_pips"] == 0.8


def test_slippage_signed_adverse_sell(tmp_path):
    ta = TradeAnalytics(log_dir=str(tmp_path))
    # SELL filled BELOW signal = worse fill = positive adverse slippage
    ta.record_entry(2, "EURUSD", "SELL", 1.10000, 1.10080, 1.09840, _snap(),
                    spread_pips=0.5, fill_price=1.09996)
    ta.record_exit(2, 1.09950, 5.0, "Take profit", _snap())
    rec = _read_last(ta._log_file)
    assert rec["entry_slippage_pips"] == 0.4


def test_protect_floor_ref_scales_and_clamps(tmp_path):
    ta = TradeAnalytics(log_dir=str(tmp_path))
    for tk, vp, expect in [(10, 0.9, 3.6), (11, 5.0, 12.0), (12, 0.2, 2.0)]:
        ta.record_entry(tk, "EURUSD", "BUY", 1.1, 1.0999, 1.1002, _snap(vol_pips=vp))
        ta.record_exit(tk, 1.1001, 1.0, "Take profit", _snap())
        rec = _read_last(ta._log_file)
        assert rec["profit_protect_pips_ref"] == expect, (vp, rec["profit_protect_pips_ref"])


def test_backward_compatible_without_new_args(tmp_path):
    ta = TradeAnalytics(log_dir=str(tmp_path))
    ta.record_entry(3, "EURUSD", "BUY", 1.10000, 1.09920, 1.10160, _snap())
    ta.record_exit(3, 1.10050, 5.0, "Take profit", _snap())
    rec = _read_last(ta._log_file)
    assert rec["entry_fill_price"] == 0.0        # not supplied → default
    assert rec["entry_slippage_pips"] == 0.0
    assert rec["profit_protect_pips_ref"] == 3.6  # still computed from vol_pips
