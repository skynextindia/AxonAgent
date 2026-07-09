"""Tests for per-pair scaling + graded fail-open reversal gate (WS1/WS4)."""

from types import SimpleNamespace

from axonai.realtime.reversal_model import _reversal_confluence_grade
from axonai.realtime.displacement_engine import DisplacementEngine


def _mtf(h4=0.6, h1=0.4, m15=0.3, revp=0.0, exh=False, sb=False, sbdir=0):
    return SimpleNamespace(
        h4_bias=h4, h1_bias=h1, m15_bias=m15,
        is_exhaustion_zone=exh, is_pullback=False,
        reversal_pressure=revp, structure_break=sb, structure_break_dir=sbdir,
    )


def _liq(sweeps=0, breaks=0, void=False):
    return SimpleNamespace(
        active_sweeps=[SimpleNamespace(direction="support", price=1.0)] * sweeps,
        active_breaks=[object()] * breaks,
        liquidity_void_active=void,
    )


def _vel(decaying=False, unusual=False):
    return SimpleNamespace(is_decaying=decaying, is_unusual=unusual, vol_pips=1.0)


def _disp(cls="EXHAUSTION", ratio=0.2, exhausting=True):
    return SimpleNamespace(classification=cls, displacement_ratio=ratio,
                           is_exhausting=exhausting, net_displacement_pips=-3.0)


def _lvl(direction, tf, is_active=True, price=1.1000):
    return SimpleNamespace(direction=direction, timeframe=tf, level_type="",
                           is_active=is_active, price=price)


def test_falling_knife_hard_rejected():
    allow, score, reason = _reversal_confluence_grade(
        "SELL", 1.1000, 0.0001, 0.0010, _mtf(h4=-0.6, h1=-0.4),
        _liq(breaks=1), _vel(), _disp(), None, {})
    assert allow is False and "falling knife" in reason


def test_velocity_spike_in_void_rejected():
    allow, score, reason = _reversal_confluence_grade(
        "BUY", 1.1000, 0.0001, 0.0010, _mtf(),
        _liq(void=True), _vel(unusual=True), _disp(), None, {})
    assert allow is False and "void" in reason


def test_counter_trend_without_exhaustion_rejected():
    # SELL against a strong bull trend, no exhaustion/pressure/structure -> reject
    allow, score, reason = _reversal_confluence_grade(
        "SELL", 1.1000, 0.0001, 0.0010, _mtf(h4=0.8, h1=0.7, revp=0.0),
        _liq(), _vel(), _disp(cls="NEUTRAL", exhausting=False), None, {})
    assert allow is False and "against big-TF trend" in reason


def test_counter_trend_with_reversal_pressure_allowed():
    # Same counter-trend but reversal_pressure clears the exhaustion requirement
    allow, score, reason = _reversal_confluence_grade(
        "SELL", 1.1000, 0.0001, 0.0010, _mtf(h4=0.8, h1=0.7, revp=0.7),
        _liq(sweeps=1), _vel(decaying=True), _disp(), None, {})
    assert allow is True


def test_no_levels_but_sharp_confirmation_allowed_failopen():
    # With-trend, no levels synced, sharp sweep+exhaustion -> fail-open allow
    allow, score, reason = _reversal_confluence_grade(
        "BUY", 1.1000, 0.0001, 0.0010, _mtf(h4=0.6, h1=0.4),
        _liq(sweeps=1), _vel(decaying=True), _disp(), None, {})
    assert allow is True


def test_single_major_level_allows():
    levels = [_lvl("support", "H4")]  # major only, no micro
    allow, score, reason = _reversal_confluence_grade(
        "BUY", 1.1000, 0.0001, 0.0010, _mtf(h4=0.6, h1=0.4),
        _liq(sweeps=1), _vel(decaying=True), _disp(), levels, {})
    assert allow is True


def test_displacement_trend_threshold_is_pair_scaled():
    # On a gold-scaled config, a net move of 5 "pips" must NOT flip trend
    # (FX threshold 2.0 would call it a trend; scaled 20.0 keeps it 'mixed').
    eng = DisplacementEngine(pip_mult=0.01, config={"displacement_trend_net_pips": 20.0})
    for _ in range(20):
        eng._displacement_history.append(0.25)  # 20 * 0.25 = 5.0 net
    assert eng.get_recent_trend(lookback=20) == "mixed"
    # FX-default engine would call the same 5.0 net a trend
    eng_fx = DisplacementEngine(pip_mult=0.0001, config={})
    for _ in range(20):
        eng_fx._displacement_history.append(0.25)
    assert eng_fx.get_recent_trend(lookback=20) == "bullish"
