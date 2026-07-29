"""Room>=1p veto and TREND_CONTINUATION fade veto (enabled 2026-07-30).

Both are pre-existing gates in _unified_confluence_score, switched on via config
(entry_min_room_pips=1.0, entry_avoid_regimes=["TREND_CONTINUATION"]). These
lock the enabled behavior: fades into a trend regime and fades with <1p room to
target are rejected, while chop fades with room are not rejected for those reasons.
"""
from types import SimpleNamespace

from axonai.realtime.reversal_model import _unified_confluence_score


def _mtf():
    return SimpleNamespace(h4_bias=1.0, h1_bias=1.0, m15_bias=1.0, reversal_pressure=1.0,
                           is_exhaustion_zone=True, is_pullback=False)


def _liq():
    return SimpleNamespace(active_breaks=[], active_sweeps=[], liquidity_void_active=False)


def _vel():
    return SimpleNamespace(is_unusual=False, percentile=50.0, decay_ratio=1.0,
                           tick_efficiency=0.2, vol_pips=1.0)


def _disp():
    return SimpleNamespace(classification="EXHAUSTION", displacement_ratio=0.1,
                           net_displacement_pips=1.0)


def _call(config, regime="RANGE_CHOP", room_above=5.0):
    lc = SimpleNamespace(room_above_pips=room_above, room_below_pips=room_above,
                         room_available=room_above)
    return _unified_confluence_score(
        direction="BUY", price=1.1, pip=0.0001, h1_atr=0.001,
        mtf=_mtf(), liq=_liq(), vel=_vel(), disp=_disp(), price_levels=None,
        candle_setup_score=1.0, config=config, location_context=lc, regime=regime,
    )


ROOM = {"entry_min_room_pips": 1.0}
AVOID = {"entry_avoid_regimes": ["TREND_CONTINUATION"]}


def test_trend_continuation_fade_vetoed():
    ok, _, why = _call(AVOID, regime="TREND_CONTINUATION")
    assert ok is False and "avoided regime" in why


def test_chop_not_regime_vetoed():
    ok, _, why = _call(AVOID, regime="RANGE_CHOP")
    assert "avoided regime" not in why  # may fail later gates, but not on regime


def test_low_room_vetoed():
    ok, _, why = _call(ROOM, room_above=0.5)
    assert ok is False and "insufficient room" in why


def test_sufficient_room_not_room_vetoed():
    ok, _, why = _call(ROOM, room_above=2.0)
    assert "insufficient room" not in why
