"""Corr-gate logic tests (pure, no MT5). These mirror daemon._pair_correlation /
_corr_gate_eval and the reader's saving calc — if the daemon logic changes, update here."""
import math


def pearson(a, b):
    n = len(a); ma = sum(a) / n; mb = sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a); vb = sum((x - mb) ** 2 for x in b)
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else 0.0


def verdict(corr, thr):
    # mirrors daemon._corr_gate_eval: tight (<= thr) -> skip; missing -> fire (fail-open)
    return "skip" if (corr is not None and corr <= thr) else "fire"


def arming_saving(skip_rows_follower_pnls):
    # mirrors reader: arming drops the follower leg on SKIP rows -> saving = -sum(follower)
    return -sum(skip_rows_follower_pnls)


def test_pearson_extremes():
    a = [0.1, -0.2, 0.3, -0.1, 0.2]
    assert round(pearson(a, a), 6) == 1.0
    assert round(pearson(a, [-x for x in a]), 6) == -1.0


def test_verdict_rule():
    assert verdict(-0.82, -0.70) == "skip"    # lockstep -> skip the 2nd leg
    assert verdict(-0.70, -0.70) == "skip"    # boundary inclusive
    assert verdict(-0.55, -0.70) == "fire"    # loose -> keep
    assert verdict(+0.10, -0.70) == "fire"
    assert verdict(None, -0.70) == "fire"     # data gap -> fail-open, never block


def test_arming_saving_sign():
    # follower LOST on lockstep fires -> arming adds it back (positive saving)
    assert arming_saving([-104.5, -98.0, -101.0]) > 0
    # follower WON on lockstep fires -> arming costs money (negative saving)
    assert arming_saving([+94.1, +97.0]) < 0


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
