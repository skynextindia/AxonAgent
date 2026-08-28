"""Sanity tests for the cut simulation. Pure, no I/O, no live path."""
from research.direction_location_forensics.loader import ForensicTrade
from research.exit_cut_forensics import cut_sim as C


def _t(**kw):
    d = dict(symbol="EURUSD", direction="SELL", entry_price=1.15000,
             sr_level_price=1.15030, stop_pips=20.0, pips=-20.0, mae_pips=19.0)
    d.update(kw)
    return ForensicTrade(**d)


def test_adverse_side_sell_resistance_above():
    # level 3p above a SELL -> adverse side, dist ~3
    assert round(C.adverse_side_dist_pips(_t()), 1) == 3.0


def test_sell_into_support_excluded():
    # level BELOW a SELL is not the adverse side -> rule N/A
    t = _t(sr_level_price=1.14970)
    assert C.adverse_side_dist_pips(t) is None
    assert C.simulate_trade(t, 3.0).fired is False


def test_full_stop_loser_is_rescued():
    # MAE ran to the wall; buffer 3 -> cut at -(3+3)=-6 vs actual -20 -> saver
    r = C.simulate_trade(_t(mae_pips=19.0, pips=-20.0), 3.0)
    assert r.fired and r.kind == "saver"
    assert r.cut_pips == -6.0 and r.delta == 14.0


def test_winner_that_poked_is_a_casualty():
    # small win but adverse poked past level+buffer -> naive cut clips it
    r = C.simulate_trade(_t(mae_pips=7.0, pips=5.0), 3.0)
    assert r.fired and r.kind == "casualty" and r.delta == -11.0


def test_no_fire_when_adverse_never_reached_break():
    r = C.simulate_trade(_t(mae_pips=2.0, pips=1.0), 3.0)
    assert r.fired is False


def test_no_fire_when_stop_tighter_than_break():
    # stop 4p is tighter than break thresh (3+3=6) -> price stops before cut
    r = C.simulate_trade(_t(stop_pips=4.0, mae_pips=4.0, pips=-4.0), 3.0)
    assert r.fired is False


def test_buy_support_below_geometry():
    t = _t(direction="BUY", entry_price=1.15000, sr_level_price=1.14970,
           pips=-20.0, mae_pips=19.0)
    assert round(C.adverse_side_dist_pips(t), 1) == 3.0
    assert C.simulate_trade(t, 3.0).kind == "saver"


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
