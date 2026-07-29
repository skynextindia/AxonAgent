"""Currency-exposure cap (2026-07-30): stop stacking correlated USD bets.

All 4 traded pairs share a USD leg, so nominally-opposite trades can be the same
macro bet. On 07-30, USDJPY BUY (+USD) and AUDUSD SELL (+USD) were both 'long
USD' and stopped out together (-200 in 23min). This cap nets signed units per
currency across open + intended positions and blocks exceeding the limit.
"""
from axonai.realtime.portfolio_guard import PortfolioGuard

BUY, SELL = 0, 1  # MT5 POSITION_TYPE_BUY / _SELL


def _pos(symbol, t):
    return {"symbol": symbol, "type": t}


def _guard(cap=1):
    return PortfolioGuard({
        "max_concurrent_positions": 0,
        "max_daily_loss_usd": 0,
        "max_same_direction_positions": 0,
        "max_currency_exposure": cap,
    })


def test_tonight_scenario_blocked():
    # Open USDJPY BUY (+USD); intended AUDUSD SELL (+USD) -> net USD +2 > 1
    ok, why = _guard(1).check([_pos("USDJPY", BUY)], 0.0,
                              intended_direction="SELL", intended_symbol="AUDUSD")
    assert ok is False and "currency_exposure" in why and "USD" in why


def test_same_side_usd_stack_blocked():
    # EURUSD SELL (+USD) open; GBPUSD SELL (+USD) intended -> +2 USD
    ok, why = _guard(1).check([_pos("EURUSD", SELL)], 0.0,
                              intended_direction="SELL", intended_symbol="GBPUSD")
    assert ok is False and "USD" in why


def test_offsetting_usd_allowed():
    # USDJPY BUY (+USD) open; EURUSD BUY (-USD) intended -> net USD 0
    ok, why = _guard(1).check([_pos("USDJPY", BUY)], 0.0,
                              intended_direction="BUY", intended_symbol="EURUSD")
    assert ok is True, why


def test_single_position_allowed():
    ok, why = _guard(1).check([], 0.0, intended_direction="BUY", intended_symbol="EURUSD")
    assert ok is True, why


def test_disabled_cap_allows_stack():
    ok, why = _guard(0).check([_pos("USDJPY", BUY)], 0.0,
                              intended_direction="SELL", intended_symbol="AUDUSD")
    assert ok is True, why


def test_cap_2_permits_one_doubling():
    # cap=2 lets tonight's +2 USD through (documents the knob)
    ok, why = _guard(2).check([_pos("USDJPY", BUY)], 0.0,
                              intended_direction="SELL", intended_symbol="AUDUSD")
    assert ok is True, why
