"""Tests for the good-spot selector decision (pure, deterministic)."""
from research.mtf_regime_switch.good_spot import good_spot_decision, htf_trend

HK = "1D"  # tests stamp the 1D key and pass htf_key=HK explicitly


def _stamp(tfs):
    return {"tfs": tfs}


def test_range_takes_either_direction():
    s = _stamp({HK: ["RANGE", 50.0, 0.1, 0.05]})
    assert good_spot_decision("Sell", s, htf_key=HK)["action"] == "take"
    assert good_spot_decision("Buy", s, htf_key=HK)["action"] == "take"


def test_sell_rally_in_downtrend_takes():
    s = _stamp({HK: ["DOWN", 20.0, -0.6, 0.4]})
    assert good_spot_decision("Sell", s, htf_key=HK)["action"] == "take"


def test_buy_dip_in_uptrend_skips_by_default():
    # DEFAULT skip_up_buy=True: the falsified -4p bucket is skipped.
    s = _stamp({HK: ["UP", 80.0, 0.7, 0.4]})
    d = good_spot_decision("Buy", s, htf_key=HK)
    assert d["action"] == "skip" and "uptrend" in d["reason"]


def test_buy_dip_in_uptrend_takes_when_symmetric():
    s = _stamp({HK: ["UP", 80.0, 0.7, 0.4]})
    d = good_spot_decision("Buy", s, htf_key=HK, skip_up_buy=False)
    assert d["action"] == "take"


def test_buy_into_downtrend_skips():
    s = _stamp({HK: ["DOWN", 20.0, -0.6, 0.4]})
    d = good_spot_decision("Buy", s, htf_key=HK)
    assert d["action"] == "skip" and "counter" in d["reason"]


def test_sell_into_uptrend_skips():
    s = _stamp({HK: ["UP", 80.0, 0.7, 0.4]})
    assert good_spot_decision("Sell", s, htf_key=HK)["action"] == "skip"


def test_flip_counter_trend_flips_direction():
    s = _stamp({HK: ["UP", 90.0, 0.8, 0.5]})
    d = good_spot_decision("Sell", s, htf_key=HK, flip_counter_trend=True)
    assert d["action"] == "flip" and d["flip_to"] == "Buy"
    s2 = _stamp({HK: ["DOWN", 10.0, -0.8, 0.5]})
    d2 = good_spot_decision("Buy", s2, htf_key=HK, flip_counter_trend=True)
    assert d2["action"] == "flip" and d2["flip_to"] == "Sell"


def test_htf_key_selectable():
    s = _stamp({"1D": ["UP", 80.0, 0.7, 0.4], "1H": ["DOWN", 20.0, -0.6, 0.4]})
    # Sell aligned to 1H DOWN -> take; but counter to 1D UP -> skip
    assert good_spot_decision("Sell", s, htf_key="1H")["action"] == "take"
    assert good_spot_decision("Sell", s, htf_key="1D")["action"] == "skip"


def test_no_htf_read_fails_open():
    assert good_spot_decision("Sell", {"tfs": {}})["action"] == "take"
    assert good_spot_decision("Sell", None)["action"] == "take"
    assert htf_trend(None) is None


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
