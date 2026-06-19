"""Pinning tests for the shared entry-quality gate (entry_gate.evaluate_peak_entry).

Locks the quality-gate behavior that BOTH the live daemon and the backtester
rely on, so the live dry-run can't silently drift from the backtested strategy.
"""

from __future__ import annotations

import types

from axonai.realtime.entry_gate import evaluate_peak_entry

PIP = 0.0001


class _Lvl:
    def __init__(self, price, is_active=True, level_type="PDH"):
        self.price = price
        self.is_active = is_active
        self.level_type = level_type


def _event(price=1.10000, direction="bearish_reversal", peak_type="microstructure_exhaustion",
           confidence=1.0, confirmed=True, intensity="HIGH", mtf="NEUTRAL",
           level_behavior=None, swing_confidence=None):
    d = {"peak_type": peak_type, "direction": direction, "peak_confidence": confidence,
         "peak_confirmed": confirmed, "intensity": intensity, "mtf_alignment": mtf}
    if level_behavior is not None:
        d["level_behavior"] = level_behavior
    if swing_confidence is not None:
        d["swing_confidence"] = swing_confidence
    return types.SimpleNamespace(price=price, details=d)


def _gate(event, levels=None, trend="sideways", regime="", active=None):
    if levels is None:
        levels = [_Lvl(1.10000)]  # right at price → 0 pips away
    return evaluate_peak_entry(event, price_levels=levels, pip_mult=PIP,
                               trend_h4=trend, dominant_regime=regime,
                               active_directions=active or [])


def test_high_quality_confirmed_peak_passes():
    d = _gate(_event())
    assert d.passed is True
    assert d.direction == "SELL"
    assert d.signal_quality >= 0.65


def test_far_from_sr_rejected():
    d = _gate(_event(), levels=[_Lvl(1.20000)])  # ~1000 pips away
    assert d.passed is False
    assert "S/R" in d.skip_reason


def test_counter_trend_rejected():
    d = _gate(_event(direction="bearish_reversal"), trend="up")  # SELL vs uptrend
    assert d.passed is False
    assert "uptrend" in d.skip_reason


def test_low_quality_below_floor_rejected():
    # HIGH, unconfirmed, conf 0.65 → 0.3 + 0.65*0.5 = 0.625 < 0.65 floor
    d = _gate(_event(confidence=0.65, confirmed=False))
    assert d.passed is False
    assert "quality" in d.skip_reason


def test_mtf_alignment_boost_lifts_over_floor():
    # 0.61 base lifted to 0.76 by aligned MTF (SELL + BEARISH)
    below = _gate(_event(confidence=0.62, confirmed=False, mtf="NEUTRAL"))
    assert below.passed is False
    boosted = _gate(_event(confidence=0.62, confirmed=False, mtf="BEARISH"))
    assert boosted.passed is True


def test_compression_regime_rejected():
    d = _gate(_event(), regime="compression")
    assert d.passed is False
    assert "compression" in d.skip_reason


def test_non_exhaustion_peak_rejected():
    d = _gate(_event(peak_type="local_swing_high"))
    assert d.passed is False
    assert "exhaustion" in d.skip_reason


def test_weakening_level_rejected():
    d = _gate(_event(level_behavior={"attack_quality": "weakening"}))
    assert d.passed is False
    assert "weakening" in d.skip_reason


def test_duplicate_direction_rejected():
    d = _gate(_event(direction="bearish_reversal"), active=["SELL"])
    assert d.passed is False
    assert "already open" in d.skip_reason


def test_indeterminate_direction_rejected():
    d = _gate(_event(direction="sideways"))
    assert d.passed is False
    assert "indeterminate" in d.skip_reason
