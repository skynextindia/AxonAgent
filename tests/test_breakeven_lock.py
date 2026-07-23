"""Breakeven / peak-lock ratchet (XE2/XE3).

Fills the dead band between the soft-gate profit-protect (~2.4p) and the momentum
trail activation (5p), where a winner used to round-trip to the initial SL. The
lock is one-way, arms off PEAK profit, and never emits a stop on the wrong side of
price. Default OFF, so with the flag off on_tick must behave exactly as before.
"""
from axonai.realtime.velocity_trailing import VelocityTrailingManager

PIP = 0.0001
ENTRY = 1.10000
INIT_SL = 1.09920  # 8p below entry


def _mgr(**cfg):
    base = {
        "be_lock_enabled": True, "be_arm_pips": 1.5, "be_offset_pips": 0.2,
        "be_lock_start_pips": 2.0, "be_lock_frac": 0.5,
        "realtime_trail_activation_pips": 5.0,
    }
    base.update(cfg)
    return VelocityTrailingManager(config=base)


def _tick(m, bid, peak=None, cur_sl=INIT_SL, ptype="BUY", **kw):
    """One on_tick with benign momentum inputs; optionally force the tracked peak."""
    t = 1
    if peak is not None:
        m._trail_state.setdefault(t, {})
        m._trail_state[t]["peak_price"] = peak
        m._trail_state[t].setdefault("retest_count", 0)
    args = dict(
        ticket=t, bid=bid, ask=bid + PIP, position_type=ptype, entry_price=ENTRY,
        initial_sl=INIT_SL, current_sl=cur_sl, velocity_percentile=50.0,
        velocity_acceleration=1.0, displacement_ratio=0.2, health_score=80.0,
        at_structure=False, lowest_price=ENTRY, pip=PIP, symbol="EURUSD",
        is_htf_aligned=False,
    )
    args.update(kw)
    return m.on_tick(**args)


def test_no_lock_before_arm():
    # Peak only +1.0p, below be_arm 1.5p -> no SL move.
    m = _mgr()
    res = _tick(m, bid=1.10010, peak=1.10010)
    assert res is None


def test_breakeven_arms_at_arm_pips():
    # Peaked +1.6p (>=1.5 arm), price pulled back to +0.5p. SL -> entry + 0.2p.
    m = _mgr()
    res = _tick(m, bid=1.10005, peak=1.10016)
    assert res is not None
    assert res["reason"] == "breakeven_lock"
    assert abs(res["new_sl"] - (ENTRY + 0.2 * PIP)) < 1e-9


def test_lock_fraction_of_peak():
    # Peaked +4.0p (>= lock_start 2.0), price back to +2.5p. Lock 0.5*4 = +2.0p.
    m = _mgr()
    res = _tick(m, bid=1.10025, peak=1.10040)
    assert res is not None
    assert abs(res["new_sl"] - (ENTRY + 2.0 * PIP)) < 1e-9


def test_one_way_ratchet_never_loosens():
    # Current SL already at +3p; a lock computing +2p must NOT pull it back down.
    m = _mgr()
    res = _tick(m, bid=1.10025, peak=1.10040, cur_sl=ENTRY + 3.0 * PIP)
    assert res is None


def test_never_emits_stop_above_price():
    # Peaked +4p but price collapsed to +0.5p; lock floor +2p is ABOVE bid -> skip
    # (an invalid BUY stop). The prior tick's SL would have closed it.
    m = _mgr()
    res = _tick(m, bid=1.10005, peak=1.10040)
    assert res is None


def test_sell_side_locks_below_entry():
    # SELL: peaked +4p (ask fell to 1.09960), bounced back so ask is now 1.09975
    # (bid 1.09965, +2.5p). Lock 0.5*4 = +2p -> SL at entry - 2p = 1.09980, which is
    # a valid SELL stop (above the 1.09975 ask).
    m = _mgr()
    res = _tick(m, bid=1.09965, peak=1.09960, ptype="SELL", cur_sl=ENTRY + 8 * PIP)
    assert res is not None
    assert abs(res["new_sl"] - (ENTRY - 2.0 * PIP)) < 1e-9


def test_disabled_is_strict_noop():
    # Same setup that would lock, but flag off -> None (no SL move at sub-activation).
    m = _mgr(be_lock_enabled=False)
    res = _tick(m, bid=1.10005, peak=1.10040)
    assert res is None
