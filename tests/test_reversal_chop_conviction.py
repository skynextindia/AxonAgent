"""Conviction floor inside an allowed chop regime (reversal_chop_conviction).

2026-07-30: AUD reversals live in RANGE_CHOP so the pair is deliberately NOT
chop-blocked, but the live losers there were NEUTRAL / reversal_pressure=0
fades. This gate demands EXHAUSTION or reversal_pressure >= floor when trading
in the configured chop regime, without closing the pair's edge window.

Tests call AxonDaemon._reversal_edge_ok bound to a lightweight fake self so the
pure gate logic runs without a full daemon init.
"""
from types import SimpleNamespace

from axonai.realtime.daemon import AxonDaemon


def _self(symbol="AUDUSD", revp=0.0):
    cfg = {
        "reversal_edge_gate_enabled": True,
        "reversal_block_regimes": {"default": ["RANGE_CHOP"], "AUDUSD": [], "EURUSD": []},
        "reversal_chop_conviction": {
            "AUDUSD": {"regimes": ["RANGE_CHOP"], "min_reversal_pressure": 0.6},
        },
        # loose floors so pass-cases are decided by the conviction gate, not floors
        "reversal_pair_floors": {
            "AUDUSD": {"vel_pct": 5, "vol_pips": 0.1, "tick_eff": 0.1},
            "EURUSD": {"vel_pct": 5, "vol_pips": 0.1, "tick_eff": 0.1},
        },
        "reversal_require_location": False,
    }
    return SimpleNamespace(
        config=cfg,
        mt5_symbol=symbol,
        _pip_mult=0.0001,
        reversal_model=SimpleNamespace(
            _last_mtf_state=SimpleNamespace(reversal_pressure=revp)
        ),
    )


def _snap(regime="RANGE_CHOP", disp="NEUTRAL"):
    return SimpleNamespace(
        regime=SimpleNamespace(regime=regime),
        displacement=SimpleNamespace(classification=disp),
        velocity=SimpleNamespace(percentile=80.0, vol_pips=1.5, tick_efficiency=0.6),
        location_context=None,
    )


def test_chop_neutral_zero_revp_blocked():
    ok, why = AxonDaemon._reversal_edge_ok(_self(revp=0.0), _snap("RANGE_CHOP", "NEUTRAL"))
    assert ok is False and "chop_low_conviction" in why


def test_chop_exhaustion_passes_even_zero_revp():
    ok, why = AxonDaemon._reversal_edge_ok(_self(revp=0.0), _snap("RANGE_CHOP", "EXHAUSTION"))
    assert ok is True, why


def test_chop_high_revp_passes():
    ok, why = AxonDaemon._reversal_edge_ok(_self(revp=0.7), _snap("RANGE_CHOP", "NEUTRAL"))
    assert ok is True, why


def test_chop_revp_below_floor_blocked():
    ok, why = AxonDaemon._reversal_edge_ok(_self(revp=0.59), _snap("RANGE_CHOP", "NEUTRAL"))
    assert ok is False and "chop_low_conviction" in why


def test_non_chop_regime_not_gated():
    # TREND_CONTINUATION is not in AUD's conviction regimes -> gate does not apply
    ok, why = AxonDaemon._reversal_edge_ok(_self(revp=0.0), _snap("TREND_CONTINUATION", "NEUTRAL"))
    assert ok is True, why


def test_other_pair_unaffected():
    # EURUSD has no conviction entry -> weak chop fade passes (byte-identical behavior)
    s = _self(symbol="EURUSD", revp=0.0)
    ok, why = AxonDaemon._reversal_edge_ok(s, _snap("RANGE_CHOP", "NEUTRAL"))
    assert ok is True, why
