"""Regression tests for DisplacementEngine._classify z-score gating.

Guards the fix for the TRAP dead-code bug: a genuine trap has a NEGATIVE
displacement z-score, but the old availability sentinel `disp_z_score > 0.0`
made the z-score trap branch unreachable, silently reverting trap detection to
the static ratio. Availability must come from a real flag (>=50 samples), not
the z's sign.
"""

from types import SimpleNamespace

from axonai.realtime.displacement_engine import (
    DisplacementEngine,
    DISPLACEMENT_TRAP,
    DISPLACEMENT_IMPULSE,
)


def _vel(z_score=0.0, is_unusual=True, is_decaying=False, tick_efficiency=0.5):
    return SimpleNamespace(
        z_score=z_score,
        is_unusual=is_unusual,
        is_decaying=is_decaying,
        tick_efficiency=tick_efficiency,
    )


def _engine():
    # Defaults: impulse_threshold=0.60, trap_threshold=0.25.
    return DisplacementEngine(pip_mult=0.0001)


def test_negative_z_is_trap_when_available():
    """z=-2.0 with z available -> TRAP, even though disp_ratio (0.30) is ABOVE the
    static trap threshold (0.25) so the static fallback would NOT flag it. This is
    exactly the case the old `disp_z_score > 0.0` sentinel silently missed."""
    eng = _engine()
    cls = eng._classify(
        _vel(is_unusual=True), disp_ratio=0.30, net_move=1.0, total_move=5.0,
        disp_z_score=-2.0, disp_z_avail=True,
    )
    assert cls == DISPLACEMENT_TRAP


def test_positive_z_is_impulse_when_available():
    eng = _engine()
    cls = eng._classify(
        _vel(is_unusual=True), disp_ratio=0.80, net_move=4.0, total_move=5.0,
        disp_z_score=2.0, disp_z_avail=True,
    )
    assert cls == DISPLACEMENT_IMPULSE


def test_cold_start_falls_back_to_static_trap():
    """z unavailable (cold start) -> static ratio path: disp_ratio 0.20 < 0.25 = TRAP."""
    eng = _engine()
    cls = eng._classify(
        _vel(is_unusual=True), disp_ratio=0.20, net_move=1.0, total_move=5.0,
        disp_z_score=0.0, disp_z_avail=False,
    )
    assert cls == DISPLACEMENT_TRAP
