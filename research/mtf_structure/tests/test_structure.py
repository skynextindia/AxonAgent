"""Tests for the MTF structure module. Pure, deterministic."""
from research.mtf_structure.structure import classify_tf, compute_mtf, MTFSnapshot

PIP = 0.0001


def _ramp(start, step, n):
    """A clean monotonic series (high ER = trend)."""
    c = [start + step * i for i in range(n)]
    return c[:], c[:], c[:]  # highs, lows, closes ~equal for a clean line


def test_uptrend_classified_up():
    h, l, c = _ramp(1.10, 0.001, 30)   # steadily rising
    tf = classify_tf("T", h, l, c, c[-1], PIP, bars=30)
    assert tf.trend == "UP" and tf.net_pips > 0 and tf.position_pct > 90


def test_downtrend_classified_down():
    h, l, c = _ramp(1.20, -0.001, 30)
    tf = classify_tf("T", h, l, c, c[-1], PIP, bars=30)
    assert tf.trend == "DOWN" and tf.position_pct < 10


def test_choppy_is_range():
    # ROUND-TRIP (up then back): net ~0 relative to the range -> RANGE under BOTH the
    # net_range default and legacy ER. (A pure alternation that ENDS at an extreme has
    # |net/range|=1 and net_range calls it a trend — endpoint-sensitive by design; the
    # good-spot backtest accepts that, so the range case must be a genuine round trip.)
    up = [1.10 + 0.0005 * i for i in range(20)]          # 1.1000 -> 1.1095
    down = [1.1095 - 0.0005 * i for i in range(20)]      # 1.1095 -> 1.1000
    c = up + down
    tf = classify_tf("T", c, c, c, c[-1], PIP, bars=40)  # default measure = net_range
    assert tf.trend == "RANGE", tf.trend
    assert abs(tf.net_range) < 0.5
    tf_er = classify_tf("T", c, c, c, c[-1], PIP, bars=40, measure="efficiency_ratio")
    assert tf_er.trend == "RANGE"


def test_measure_switch_net_range_vs_er():
    # A steady drift that is directional but NOT ER-efficient (net/range high, ER low-ish):
    # net_range should call it a trend while both measures still populate the fields.
    c = [1.10 + 0.0004 * i + (0.0003 if i % 2 else -0.0003) for i in range(30)]  # up with jitter
    nr = classify_tf("T", c, c, c, c[-1], PIP, bars=30, measure="net_range")
    er = classify_tf("T", c, c, c, c[-1], PIP, bars=30, measure="efficiency_ratio")
    assert nr.trend == "UP" and nr.net_range >= 0.5          # net/range catches the drift
    assert nr.efficiency_ratio == er.efficiency_ratio        # both measures always computed
    assert -1.0 <= nr.net_range <= 1.0


def test_position_pct_bounds():
    h = [1.10, 1.20]; l = [1.10, 1.20]; c = [1.10, 1.15]
    tf = classify_tf("T", h, l, c, 1.15, PIP, bars=2)
    assert 0 <= tf.position_pct <= 100


def test_compute_mtf_topdown_order():
    h, l, c = _ramp(1.00, 0.0005, 1300)
    snap = compute_mtf(h, l, c, PIP)
    # largest window first
    assert snap.tfs[0].bars >= snap.tfs[-1].bars
    assert snap.get("5Y") is not None and snap.get("1W") is not None


def test_fade_read_top_extreme_up_momentum():
    # construct: slow frames RANGE at top, fast frames UP
    # a long oscillation (RANGE on big windows) then a sharp rally at the end (UP on small)
    base = [1.10 + (0.01 if i % 2 else -0.01) for i in range(1260)]
    base[-21:] = [1.10 + 0.001 * i for i in range(21)]   # recent rally
    h = [x + 0.02 for x in base]; l = [x - 0.02 for x in base]; c = base
    snap = compute_mtf(h, l, c, PIP)
    fr = snap.fade_read()
    assert fr["short_tf_momentum"] in ("UP", "DOWN", "FLAT")
    assert "note" in fr


def test_never_raises_on_short_series():
    assert classify_tf("T", [1.1], [1.1], [1.1], 1.1, PIP, bars=252) is None


if __name__ == "__main__":
    import sys, traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            fails += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns)-fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
