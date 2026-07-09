import pytest
from unittest.mock import MagicMock
from axonai.realtime.velocity_trailing import VelocityTrailingManager

def test_mtf_retrace_delay():
    # Initialize manager with default config
    config = {
        "enable_mtf_retrace_delay": True,
        "mtf_retrace_threshold_pips": 1.0,
        "realtime_min_price_distance_to_trail": 2.0,
        "realtime_max_trail_distance": 15.0,
        "realtime_base_trail_buffer": 7.5,
        "realtime_min_trail_floor_pips": 4.0,
    }
    manager = VelocityTrailingManager(config=config)

    ticket = 12345
    pip = 0.0001
    entry_price = 1.1000
    initial_sl = 1.0950
    current_sl = 1.0950

    # 1. Price moves in profit to 1.1020. HTF is aligned.
    # No retrace yet. It should return a trail recommendation (or evaluate normally).
    # Since health_score is high (80) and we simulate velocity acceleration (1.5)
    res = manager.on_tick(
        ticket=ticket,
        bid=1.1020,
        ask=1.1021,
        position_type="BUY",
        entry_price=entry_price,
        initial_sl=initial_sl,
        current_sl=current_sl,
        velocity_percentile=80.0,
        velocity_acceleration=1.5,
        displacement_ratio=0.8,
        health_score=80.0,
        at_structure=False,
        lowest_price=1.1000,
        is_htf_aligned=True,
        pip=pip,
        symbol="EURUSD"
    )
    
    # Peak price should now be set to 1.1020
    assert manager._trail_state[ticket]["peak_price"] == pytest.approx(1.1020)
    assert res is not None  # It trailed because price accelerated in our direction

    # Let's say new SL is 1.0980 (e.g. from return)
    current_sl = res["new_sl"]

    # 2. Price retraces slightly to 1.1015 (0.5 pips retrace, below 1.0 threshold)
    # HTF is still aligned. It should NOT be blocked by retrace delay.
    res2 = manager.on_tick(
        ticket=ticket,
        bid=1.1015,
        ask=1.1016,
        position_type="BUY",
        entry_price=entry_price,
        initial_sl=initial_sl,
        current_sl=current_sl,
        velocity_percentile=80.0,
        velocity_acceleration=1.5,
        displacement_ratio=0.8,
        health_score=80.0,
        at_structure=False,
        lowest_price=1.1000,
        is_htf_aligned=True,
        pip=pip,
        symbol="EURUSD"
    )
    # Peak price should remain 1.1020 since 1.1015 is lower
    assert manager._trail_state[ticket]["peak_price"] == pytest.approx(1.1020)

    # 3. Price retraces further to 1.1005 (1.5 pips retrace, above 1.0 threshold)
    # HTF is aligned. Retrace delay should activate and block trailing stop.
    res3 = manager.on_tick(
        ticket=ticket,
        bid=1.1005,
        ask=1.1006,
        position_type="BUY",
        entry_price=entry_price,
        initial_sl=initial_sl,
        current_sl=current_sl,
        velocity_percentile=80.0,
        velocity_acceleration=1.5,
        displacement_ratio=0.8,
        health_score=80.0,
        at_structure=False,
        lowest_price=1.1000,
        is_htf_aligned=True,
        pip=pip,
        symbol="EURUSD"
    )
    assert res3 is None  # Delayed/suppressed!

    # 4. If HTF alignment is False, the retrace delay should NOT block trailing stop.
    # Reset current_sl to initial_sl so that the new_sl is higher and trails successfully.
    res4 = manager.on_tick(
        ticket=ticket,
        bid=1.1005,
        ask=1.1006,
        position_type="BUY",
        entry_price=entry_price,
        initial_sl=initial_sl,
        current_sl=initial_sl,
        velocity_percentile=80.0,
        velocity_acceleration=1.5,
        displacement_ratio=0.8,
        health_score=80.0,
        at_structure=False,
        lowest_price=1.1000,
        is_htf_aligned=False,
        pip=pip,
        symbol="EURUSD"
    )
    assert res4 is not None  # HTF not aligned, so retrace delay is bypassed and it trails
