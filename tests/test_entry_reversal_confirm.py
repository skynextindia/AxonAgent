"""Reversal-confirmation gate in the entry state machine (2026-07-30).

The retest used to trigger on decay+approach while price was still coming back
toward the level (for a SELL, while price was rising into the top) -- so 52% of
entries went adverse from the entry tick. This gate requires price to roll back
over in the trade's favor by entry_confirm_reversal_pips first.

Drives _evaluate_retest_wait directly with the machine pre-set to RETEST_WAIT.
"""
from types import SimpleNamespace

from axonai.realtime.entry_state_machine import (
    EntryStateMachine, STATE_RETEST_WAIT, STATE_TRIGGERED, STATE_INVALIDATED,
)

PIP = 0.0001
TOP = 1.1000  # SELL fade of a top


def _esm(confirm=True):
    cfg = {
        "entry_retest_require_approach": True,
        "entry_require_reversal_confirm": confirm,
        "entry_confirm_reversal_pips": 0.8,
        "entry_min_stall_duration": 15.0,
        "pair_move_scale": 1.0,
        "candle_setup_gate": True,
    }
    m = EntryStateMachine(pip_mult=PIP, config=cfg)
    m._current_state = STATE_RETEST_WAIT
    m._anomaly_price = TOP
    m._anomaly_direction = "SELL"
    m._anomaly_extreme_price = TOP
    m._retest_start_time = 0.0
    m._arm_start_time = 0.0
    m._retest_extreme = 0.0
    m._confirm_ref = None
    return m


def _vel():
    return SimpleNamespace(vol_pips=4.0, decay_ratio=0.3)  # zone=4, decayed


def _feed(m, price, ts=100.0):
    m._evaluate_retest_wait(price, ts, _vel())


def test_no_trigger_while_price_coming_back():
    m = _esm(confirm=True)
    _feed(m, 1.0990)  # pull away down (dist -10)
    _feed(m, 1.0997)  # come back up (dist -3): approach+decay ok, but no rollover
    _feed(m, 1.0998)  # further up (dist -2)
    assert m._current_state == STATE_RETEST_WAIT  # legacy would have triggered here


def test_triggers_after_rollover():
    m = _esm(confirm=True)
    _feed(m, 1.0990)   # pull away (extreme -10)
    _feed(m, 1.0997)   # come back (dist -3, ref -3)
    _feed(m, 1.0998)   # peak of come-back (ref -2)
    assert m._current_state == STATE_RETEST_WAIT
    _feed(m, 1.09965)  # rolls back over: dist -3.5 <= ref(-2) - 0.8 -> confirmed
    assert m._current_state == STATE_TRIGGERED


def test_never_turns_breaks_out_no_trigger():
    m = _esm(confirm=True)
    _feed(m, 1.0990)    # pull away
    _feed(m, 1.10025)   # price makes new high beyond extreme -> breakout kill
    assert m._current_state == STATE_INVALIDATED


def test_legacy_triggers_on_approach_without_confirm():
    m = _esm(confirm=False)
    _feed(m, 1.0990)   # pull away
    _feed(m, 1.0997)   # approach+decay, no rollover -> legacy fires
    assert m._current_state == STATE_TRIGGERED
