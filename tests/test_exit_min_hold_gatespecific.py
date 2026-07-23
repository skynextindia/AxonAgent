"""min-hold must gag only the noise cutters, never thesis_failure.

thesis_failure is net +43.5p over 109 real FX trades — the one soft gate that
works. Blanket-suppressing every gate for the hold window would throw it away.
These pin that thesis_failure fires inside the window while adverse_impulse does
not.
"""
import types
from datetime import datetime, timedelta

from axonai.realtime.exit_engine import ExitEngine


def _trade_state(**kw):
    base = dict(
        is_active=True, thesis_status="OK", last_velocity_percentile=80.0,
        last_displacement="IMPULSE", mfe=3.0, htf_context="NEUTRAL",
        ticks_in_trade=100, current_profit_pips=0.0, current_phase="ENTRY",
        last_displacement_direction_favorable=False,
        entry_time=datetime.now() - timedelta(seconds=30),  # 30s in, inside a 3600s hold
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _snapshot(tick_eff=0.6, decay=0.2):
    vel = types.SimpleNamespace(vol_pips=0.8, tick_efficiency=tick_eff, decay_ratio=decay)
    return types.SimpleNamespace(velocity=vel)


def _loc(at_structure=False):
    return types.SimpleNamespace(at_structure=at_structure)


CFG = {"exit_min_hold_seconds": 3600.0, "exit_min_hold_exempt_thesis": True,
       "exit_engine_enable_adverse_impulse": True}


def _engine(cfg=CFG):
    return ExitEngine(legacy_exit_manager=None, pip_mult=0.0001, config=cfg)


def test_thesis_failure_fires_inside_min_hold():
    eng = _engine()
    ts = _trade_state(thesis_status="BROKEN", last_displacement="ABSORPTION",
                      current_profit_pips=0.0)
    sig = eng.evaluate(ts, _snapshot(), _loc(), 1.1000)
    assert sig.should_exit is True and "Thesis failure" in sig.reason


def test_adverse_impulse_suppressed_inside_min_hold():
    eng = _engine()
    # Conditions that WOULD trigger adverse_impulse, but we are 30s into a 3600s hold.
    ts = _trade_state(thesis_status="OK", last_displacement="IMPULSE",
                      current_profit_pips=0.0, current_phase="ENTRY")
    sig = eng.evaluate(ts, _snapshot(tick_eff=0.6), _loc(), 1.1000)
    assert sig.should_exit is False


def test_adverse_impulse_fires_after_min_hold():
    eng = _engine()
    ts = _trade_state(thesis_status="OK", last_displacement="IMPULSE",
                      current_profit_pips=0.0, current_phase="ENTRY",
                      entry_time=datetime.now() - timedelta(seconds=7200))  # past the hold
    sig = eng.evaluate(ts, _snapshot(tick_eff=0.6), _loc(), 1.1000)
    assert sig.should_exit is True and "Adverse impulse" in sig.reason


def test_legacy_mode_suppresses_everything():
    eng = _engine({**CFG, "exit_min_hold_exempt_thesis": False})
    ts = _trade_state(thesis_status="BROKEN", last_displacement="ABSORPTION")
    sig = eng.evaluate(ts, _snapshot(), _loc(), 1.1000)
    assert sig.should_exit is False and "MIN_HOLD" in sig.reason
