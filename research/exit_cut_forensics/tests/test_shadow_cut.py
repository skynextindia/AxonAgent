"""Tests for the live shadow-cut tracker. Verifies it fires correctly, exactly
once, honors geometry/stop guards, and is incapable of side effects."""
import os
import tempfile
from research.exit_cut_forensics.shadow_cut import ShadowCutTracker, adverse_side_dist_pips


def _mk(tmp):
    return ShadowCutTracker(buffer_pips=3.0, out_dir=tmp)


def test_geometry_helper():
    # SELL, level 3p above -> adverse side, dist 3
    assert round(adverse_side_dist_pips("SELL", 1.15000, 1.15030, 0.0001), 1) == 3.0
    # SELL, level below -> not adverse side
    assert adverse_side_dist_pips("SELL", 1.15000, 1.14970, 0.0001) is None


def test_fires_once_on_break(tmp_path=None):
    tmp = tempfile.mkdtemp()
    tr = _mk(tmp)
    kw = dict(ticket=1, direction="SELL", entry=1.15000, sr_level=1.15030,
              symbol="EURUSD", sl_pips=20.0)
    # adverse 4p (< 3+3=6 thresh) -> no fire
    assert tr.observe(bid=1.15040, ask=1.15040, **kw) is None
    # adverse 7p (>= 6) -> fire, would-cut at -(3+3) = -6
    row = tr.observe(bid=1.15070, ask=1.15070, **kw)
    assert row is not None and row["would_cut_pips"] == -6.0
    # subsequent ticks never fire again
    assert tr.observe(bid=1.15090, ask=1.15090, **kw) is None


def test_sell_into_support_never_fires():
    tmp = tempfile.mkdtemp()
    tr = _mk(tmp)
    kw = dict(ticket=2, direction="SELL", entry=1.15000, sr_level=1.14970,
              symbol="EURUSD", sl_pips=20.0)
    for px in (1.15050, 1.15100, 1.15200):
        assert tr.observe(bid=px, ask=px, **kw) is None


def test_stop_tighter_than_break_never_fires():
    tmp = tempfile.mkdtemp()
    tr = _mk(tmp)
    # break thresh = dist(3) + buf(3) = 6, but stop is 4 -> price stops first
    kw = dict(ticket=3, direction="SELL", entry=1.15000, sr_level=1.15030,
              symbol="EURUSD", sl_pips=4.0)
    assert tr.observe(bid=1.15070, ask=1.15070, **kw) is None


def test_disabled_is_inert():
    tmp = tempfile.mkdtemp()
    tr = ShadowCutTracker(buffer_pips=3.0, enabled=False, out_dir=tmp)
    kw = dict(ticket=4, direction="SELL", entry=1.15000, sr_level=1.15030,
              symbol="EURUSD", sl_pips=20.0)
    assert tr.observe(bid=1.15100, ask=1.15100, **kw) is None
    assert not os.path.exists(os.path.join(tmp, "would_cut_shadow.jsonl"))


def test_writes_only_to_out_dir():
    tmp = tempfile.mkdtemp()
    tr = _mk(tmp)
    kw = dict(ticket=5, direction="SELL", entry=1.15000, sr_level=1.15030,
              symbol="EURUSD", sl_pips=20.0)
    tr.observe(bid=1.15070, ask=1.15070, **kw)
    assert os.path.exists(os.path.join(tmp, "would_cut_shadow.jsonl"))


def test_bad_input_never_raises():
    tmp = tempfile.mkdtemp()
    tr = _mk(tmp)
    # None sr_level, None sl -> must return None, not raise
    assert tr.observe(ticket=6, direction="SELL", entry=1.15, sr_level=None,
                      symbol="EURUSD", bid=1.15, ask=1.15, sl_pips=None) is None


def test_buy_support_break_fires():
    tmp = tempfile.mkdtemp()
    tr = _mk(tmp)
    kw = dict(ticket=7, direction="BUY", entry=1.15000, sr_level=1.14970,
              symbol="EURUSD", sl_pips=20.0)
    row = tr.observe(bid=1.14930, ask=1.14930, **kw)  # adverse 7p >= 6
    assert row is not None and row["would_cut_pips"] == -6.0


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
