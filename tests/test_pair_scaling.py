"""Tests for per-pair scaling + the unified confluence gate.

These were written against the old graded gate `_reversal_confluence_grade`.
That name is now an alias for `_unified_confluence_score`, which has different
semantics: a weighted 4-component score (candle setup 0.30, velocity exhaustion
0.25, level proximity 0.25, MTF alignment 0.20) against a 0.65 threshold, rather
than a fail-open grade. The calls also passed `{}` positionally into what is now
`candle_setup_score`, so every case raised TypeError before asserting anything.

Rewritten to exercise the gate that actually runs. The hard rejects are
unchanged in intent; the allow cases now supply scores that genuinely clear the
threshold instead of relying on fail-open behaviour that no longer exists.
"""

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


def _vel(decaying=False, unusual=False, tick_efficiency=0.5, decay_ratio=1.0):
    return SimpleNamespace(is_decaying=decaying, is_unusual=unusual, vol_pips=1.0,
                           tick_efficiency=tick_efficiency, decay_ratio=decay_ratio,
                           percentile=50.0)


def _disp(cls="EXHAUSTION", ratio=0.2, exhausting=True):
    return SimpleNamespace(classification=cls, displacement_ratio=ratio,
                           is_exhausting=exhausting, net_displacement_pips=-3.0)


def _lvl(direction, tf, is_active=True, price=1.1000):
    return SimpleNamespace(direction=direction, timeframe=tf, level_type="",
                           is_active=is_active, price=price)


def _loc(room=None, above=None, below=None):
    # room sets the direction-agnostic field; above/below set the directional ones.
    # Default the directional fields to `room` so single-arg callers still work.
    return SimpleNamespace(
        room_available=room if room is not None else 10.0,
        room_above_pips=above if above is not None else (room if room is not None else 10.0),
        room_below_pips=below if below is not None else (room if room is not None else 10.0),
    )


# ── Hard rejects ───────────────────────────────────────────────────────────

def test_falling_knife_hard_rejected():
    # Trend-aligned so the "no setup and not aligned" reject does not pre-empt it.
    allow, score, reason = _reversal_confluence_grade(
        "SELL", 1.1000, 0.0001, 0.0010, _mtf(h4=-0.6, h1=-0.4),
        _liq(breaks=1), _vel(), _disp(), None, config={})
    assert allow is False and "falling knife" in reason


def test_velocity_spike_in_void_rejected():
    allow, score, reason = _reversal_confluence_grade(
        "BUY", 1.1000, 0.0001, 0.0010, _mtf(),
        _liq(void=True), _vel(unusual=True), _disp(), None, config={})
    assert allow is False and "void" in reason


def test_no_setup_and_not_aligned_rejected():
    # Counter-trend with no candle setup: rejected before any scoring.
    allow, score, reason = _reversal_confluence_grade(
        "SELL", 1.1000, 0.0001, 0.0010, _mtf(h4=0.8, h1=0.7),
        _liq(), _vel(), _disp(), None, candle_setup_score=0.0, config={})
    assert allow is False and "no active candle setup" in reason


def test_counter_trend_without_exhaustion_rejected():
    # SELL against a strong bull trend with no exhaustion evidence anywhere.
    # A candle setup is supplied so this reaches the counter-trend reject rather
    # than tripping the not-aligned reject above.
    allow, score, reason = _reversal_confluence_grade(
        "SELL", 1.1000, 0.0001, 0.0010, _mtf(h4=0.8, h1=0.7, revp=0.0),
        _liq(), _vel(), _disp(cls="NEUTRAL", exhausting=False), None,
        candle_setup_score=0.8, config={})
    assert allow is False and "counter-trend without exhaustion" in reason


# ── Allows ─────────────────────────────────────────────────────────────────

def test_counter_trend_with_reversal_pressure_allowed():
    # Same counter-trend, but reversal_pressure clears the exhaustion requirement
    # and counter-trend-with-exhaustion earns full MTF credit (the reversal thesis).
    allow, score, reason = _reversal_confluence_grade(
        "SELL", 1.1000, 0.0001, 0.0010, _mtf(h4=0.8, h1=0.7, revp=0.7),
        _liq(sweeps=1), _vel(decaying=True, tick_efficiency=0.1), _disp(), None,
        candle_setup_score=1.0, config={})
    assert allow is True, reason


def test_no_levels_but_strong_setup_and_exhaustion_allows():
    # With-trend, no levels synced. Setup 0.30 + velocity 0.20 + MTF 0.20 = 0.70.
    allow, score, reason = _reversal_confluence_grade(
        "BUY", 1.1000, 0.0001, 0.0010, _mtf(h4=0.6, h1=0.4),
        _liq(sweeps=1), _vel(decaying=True, tick_efficiency=0.1), _disp(), None,
        candle_setup_score=1.0, config={})
    assert allow is True, reason


def test_single_major_level_allows():
    # A level at the price contributes proximity credit, so a weaker setup clears.
    levels = [_lvl("support", "H4")]  # major only, no micro
    allow, score, reason = _reversal_confluence_grade(
        "BUY", 1.1000, 0.0001, 0.0010, _mtf(h4=0.6, h1=0.4),
        _liq(sweeps=1), _vel(decaying=True), _disp(), levels,
        candle_setup_score=0.5, config={})
    assert allow is True, reason


def test_level_proximity_actually_contributes():
    """Same inputs with and without the level; the level must raise the score."""
    kw = dict(candle_setup_score=0.5, config={})
    _, without, _ = _reversal_confluence_grade(
        "BUY", 1.1000, 0.0001, 0.0010, _mtf(h4=0.6, h1=0.4),
        _liq(sweeps=1), _vel(decaying=True), _disp(), None, **kw)
    _, with_level, _ = _reversal_confluence_grade(
        "BUY", 1.1000, 0.0001, 0.0010, _mtf(h4=0.6, h1=0.4),
        _liq(sweeps=1), _vel(decaying=True), _disp(), [_lvl("support", "H4")], **kw)
    assert with_level > without


# ── Correlated-velocity dedupe ─────────────────────────────────────────────

def test_correlated_velocity_signals_are_not_double_counted():
    """is_decaying and decay<0.5 are one event; summing them inflates the score."""
    args = ("BUY", 1.1000, 0.0001, 0.0010, _mtf(h4=0.6, h1=0.4),
            _liq(sweeps=1), _vel(decaying=True, decay_ratio=0.0), _disp(), None)
    _, deduped, _ = _reversal_confluence_grade(
        *args, candle_setup_score=0.5, config={"entry_dedupe_correlated_velocity": True})
    _, summed, _ = _reversal_confluence_grade(
        *args, candle_setup_score=0.5, config={"entry_dedupe_correlated_velocity": False})
    assert deduped < summed


def test_zero_valued_velocity_fields_are_not_treated_as_missing():
    """0.0 is the strongest reading for these fields, not a missing value.

    `getattr(v, "decay_ratio", 1.0) or 1.0` mapped total decay (0.0) to no decay
    (1.0), and tick_efficiency 0.0 (a pure climax tick) to 0.5 (no climax credit),
    inverting both signals exactly when they were most informative.
    """
    common = ("BUY", 1.1000, 0.0001, 0.0010, _mtf(h4=0.6, h1=0.4),
              _liq(sweeps=1))
    _, extreme, _ = _reversal_confluence_grade(
        *common, _vel(decaying=True, decay_ratio=0.0, tick_efficiency=0.0), _disp(), None,
        candle_setup_score=0.5, config={})
    _, mild, _ = _reversal_confluence_grade(
        *common, _vel(decaying=True, decay_ratio=0.9, tick_efficiency=0.9), _disp(), None,
        candle_setup_score=0.5, config={})
    assert extreme > mild


# ── Room-to-next-level veto ────────────────────────────────────────────────

def _allow_with_room(room, min_room):
    return _reversal_confluence_grade(
        "BUY", 1.1000, 0.0001, 0.0010, _mtf(h4=0.6, h1=0.4),
        _liq(sweeps=1), _vel(decaying=True, tick_efficiency=0.1), _disp(), None,
        candle_setup_score=1.0, config={"entry_min_room_pips": min_room},
        location_context=_loc(room))


def test_room_veto_blocks_boxed_in_entry():
    allow, score, reason = _allow_with_room(room=0.4, min_room=0.8)
    assert allow is False and "room" in reason.lower()


def test_room_veto_allows_when_room_sufficient():
    allow, _, _ = _allow_with_room(room=1.5, min_room=0.8)
    assert allow is True


def test_room_veto_ignores_no_levels_sentinel():
    # room_available == 10.0 means no levels synced (open space), not boxed in.
    allow, _, _ = _allow_with_room(room=10.0, min_room=0.8)
    assert allow is True


def test_room_veto_disabled_by_default():
    # min_room 0.0 -> even zero room does not veto on the room check.
    allow, _, _ = _allow_with_room(room=0.1, min_room=0.0)
    assert allow is True


def _grade(direction, loc, min_room):
    mtf = _mtf(h4=0.6, h1=0.4) if direction == "BUY" else _mtf(h4=-0.6, h1=-0.4)
    return _reversal_confluence_grade(
        direction, 1.1000, 0.0001, 0.0010, mtf,
        _liq(sweeps=1), _vel(decaying=True, tick_efficiency=0.1), _disp(), None,
        candle_setup_score=1.0, config={"entry_min_room_pips": min_room},
        location_context=loc)


def test_room_veto_is_direction_aware_buy_reads_room_above():
    # BUY profits UP: boxed in above (0.4p to resistance) but open below -> veto.
    allow, _, reason = _grade("BUY", _loc(above=0.4, below=8.0), min_room=1.0)
    assert allow is False and "room" in reason.lower()


def test_room_veto_is_direction_aware_buy_ignores_room_below():
    # BUY with tight room BELOW (support just under) but open ABOVE -> allowed.
    allow, _, _ = _grade("BUY", _loc(above=5.0, below=0.3), min_room=1.0)
    assert allow is True


def test_room_veto_is_direction_aware_sell_reads_room_below():
    # SELL profits DOWN: boxed in below (0.4p to support) -> veto.
    allow, _, reason = _grade("SELL", _loc(above=8.0, below=0.4), min_room=1.0)
    assert allow is False and "room" in reason.lower()


def test_room_veto_is_direction_aware_sell_ignores_room_above():
    # SELL with tight room ABOVE but open BELOW -> allowed.
    allow, _, _ = _grade("SELL", _loc(above=0.3, below=5.0), min_room=1.0)
    assert allow is True


# ── Pair scaling ───────────────────────────────────────────────────────────

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
