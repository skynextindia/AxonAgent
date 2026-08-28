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
    # oscillating series: net ~0, low ER -> RANGE
    c = [1.10 + (0.005 if i % 2 else -0.005) for i in range(40)]
    tf = classify_tf("T", c, c, c, c[-1], PIP, bars=40)
    assert tf.trend == "RANGE"


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
