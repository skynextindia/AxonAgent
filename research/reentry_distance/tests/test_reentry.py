"""Pure tests for reentry_core (no MT5). If the core logic changes, update here."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from reentry_core import pip_size, annotate_reentries, is_near_reentry, summarize


def _pos(sym, d, et, epx, xt, xpx, net):
    return {"sym": sym, "dir": d, "entry_t": et, "entry_px": epx,
            "exit_t": xt, "exit_px": xpx, "net": net}


def test_pip_size():
    assert pip_size("EURUSD.i") == 0.0001
    assert pip_size("USDJPY.i") == 0.01


def test_distance_from_prior_same_dir_exit():
    # SELL exits at 1.16137, next SELL opens 1.16119 16 min later -> 1.8p / 16min
    p1 = _pos("EURUSD.i", "SELL", 1000, 1.16185, 2000, 1.16137, +20.0)
    p2 = _pos("EURUSD.i", "SELL", 2000 + 16 * 60, 1.16119, 9000, 1.16319, -105.0)
    ann = annotate_reentries([p2, p1])   # unordered input on purpose
    a2 = [r for r in ann if r["entry_t"] == p2["entry_t"]][0]
    assert a2["dist_pips"] == 1.8
    assert a2["gap_min"] == 16.0
    # the first trade has no prior same-dir exit
    a1 = [r for r in ann if r["entry_t"] == p1["entry_t"]][0]
    assert a1["dist_pips"] is None


def test_direction_and_symbol_are_scoped():
    # a BUY exit must not be the reference for a SELL entry
    b = _pos("EURUSD.i", "BUY", 1000, 1.1600, 2000, 1.1620, +10)
    s = _pos("EURUSD.i", "SELL", 3000, 1.1621, 4000, 1.1610, +5)
    ann = annotate_reentries([b, s])
    a_s = [r for r in ann if r["dir"] == "SELL"][0]
    assert a_s["dist_pips"] is None   # no prior SELL exit


def test_near_flag_needs_both_thresholds():
    r = {"dist_pips": 1.8, "gap_min": 16.0}
    assert is_near_reentry(r, 5.0, 60.0) is True
    assert is_near_reentry(r, 5.0, 10.0) is False   # too slow
    assert is_near_reentry(r, 1.0, 60.0) is False   # too far
    assert is_near_reentry({"dist_pips": None, "gap_min": None}, 5, 60) is False


def test_summarize_buckets_and_net():
    p1 = _pos("EURUSD.i", "SELL", 1000, 1.16185, 2000, 1.16137, +20.0)
    p2 = _pos("EURUSD.i", "SELL", 2000 + 16 * 60, 1.16119, 9000, 1.16319, -105.0)  # near
    p3 = _pos("EURUSD.i", "SELL", 100000, 1.1650, 110000, 1.1640, +8.0)            # far in time
    res = summarize(annotate_reentries([p1, p2, p3]), 5.0, 60.0)
    assert res["near"]["n"] == 1 and res["near"]["net"] == -105.0
    assert res["rest"]["n"] == 2   # p1 (no prior) + p3 (too old)


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            fails += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns)-fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
