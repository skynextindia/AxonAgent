import pytest
import queue
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from axonai.realtime.event_types import LiveCandle, EventPriority, EventType
from axonai.realtime.event_detector import EventDetector
from axonai.realtime.live_state import LiveWorldState, LiveMarketEvidence


def test_bullish_engulfing_high_intensity():
    # 1. Setup mocks
    mock_state = MagicMock(spec=LiveWorldState)
    mock_state.symbol = "EURUSD=X"
    mock_state.is_initialized = True
    
    # Configure live_state mock internal _state
    mock_inner_state = MagicMock()
    mock_inner_state.session = "LDN"
    mock_inner_state.session_penalty = 1.0
    mock_state._state = mock_inner_state

    mock_evidence = MagicMock(spec=LiveMarketEvidence)
    mock_evidence.key_levels = [1.15000]

    # Create historical M15 candles
    # To trigger bullish engulfing, we need:
    # - prev candle bearish (open = 1.15025, close = 1.15010)
    # - current candle bullish (open = 1.15005, close = 1.15035)
    # - current body (3.0 pips) engulfs prev body (1.5 pips)
    # - current close (1.15035) is within 5 pips (0.0005) of 1.15000 (diff = 0.00035)
    prev_candle = LiveCandle(
        timeframe="M15",
        open_time=datetime.now() - timedelta(minutes=15),
        open=1.15025,
        high=1.15030,
        low=1.15005,
        close=1.15010,
        volume=100,
        is_closed=True
    )
    
    curr_candle = LiveCandle(
        timeframe="M15",
        open_time=datetime.now(),
        open=1.15005,
        high=1.15040,
        low=1.14995,
        close=1.15035,
        volume=120,
        is_closed=False
    )

    # 10 historical candles to compute average body size
    # We want average body size to be small so momentum ratio is high (>= 1.8)
    # E.g., average body size = 0.00005 (0.5 pips)
    # Current body size = 1.15035 - 1.15005 = 0.00030 (3.0 pips) -> momentum ratio = 6.0
    m15_history = []
    for i in range(10):
        m15_history.append(
            LiveCandle(
                timeframe="M15",
                open_time=datetime.now() - timedelta(minutes=15 * (12 - i)),
                open=1.15000,
                high=1.15010,
                low=1.14990,
                close=1.15005, # body size = 0.00005
                volume=55,
                is_closed=True
            )
        )
    m15_history.append(prev_candle)
    m15_history.append(curr_candle)

    mock_evidence._m15_candles = m15_history

    event_q = queue.Queue()
    config = {
        "realtime_cooldown_seconds": 300,
        "realtime_suppress_asian": False,
        "realtime_log_events": False
    }

    detector = EventDetector(mock_state, mock_evidence, event_q, config)
    detector.set_pip_multiplier(is_jpy=False)

    # Invoke check
    detector._check_candle_pattern_at_level(curr_candle)

    # Assertions
    assert not event_q.empty()
    event = event_q.get()
    
    assert event.event_type == EventType.CANDLE_PATTERN
    assert event.priority == EventPriority.HIGH
    assert event.price == curr_candle.close
    assert event.details["pattern"] == "engulfing_bullish"
    assert event.details["at_level"] == 1.15000
    assert event.details["timeframe"] == "M15"
    assert event.details["intensity_grade"] == "HIGH"
    assert event.details["momentum_intensity"] > 1.8


def test_bearish_engulfing_medium_intensity():
    mock_state = MagicMock(spec=LiveWorldState)
    mock_state.symbol = "USDJPY"
    mock_state.is_initialized = True
    
    mock_inner_state = MagicMock()
    mock_inner_state.session = "NYC"
    mock_inner_state.session_penalty = 1.0
    mock_state._state = mock_inner_state

    # For USDJPY, pip multiplier is 0.01 (1 pip = 0.01)
    # Check within 5 pips = 0.05
    # Let's say key level is at 150.00
    # Curr close = 149.98, within 0.02 of 150.00
    mock_evidence = MagicMock(spec=LiveMarketEvidence)
    mock_evidence.key_levels = [150.00]

    # Prev bullish (open = 150.02, close = 150.05)
    # Curr bearish (open = 150.08, close = 149.98)
    # Body = 0.10
    prev_candle = LiveCandle(
        timeframe="M15",
        open_time=datetime.now() - timedelta(minutes=15),
        open=150.02,
        high=150.10,
        low=149.95,
        close=150.05,
        volume=100,
        is_closed=True
    )
    
    curr_candle = LiveCandle(
        timeframe="M15",
        open_time=datetime.now(),
        open=150.08,
        high=150.15,
        low=149.90,
        close=149.98,
        volume=120,
        is_closed=False
    )

    # 10 historical candles for average body size
    # We want average body size to trigger MEDIUM intensity: ratio around 1.3
    # E.g., average body size = 0.077. Current body = 0.10 -> ratio = 1.3
    # Only 1 historical candle should overlap 150.00. The other 9 should be located around 151.00
    m15_history = []
    
    # 1 overlapping historical candle
    m15_history.append(
        LiveCandle(
            timeframe="M15",
            open_time=datetime.now() - timedelta(minutes=15 * 12),
            open=150.00,
            high=150.10,
            low=149.90,
            close=150.077, # body size = 0.077
            volume=50,
            is_closed=True
        )
    )
    
    # 9 non-overlapping historical candles (far away from 150.00)
    for i in range(1, 10):
        m15_history.append(
            LiveCandle(
                timeframe="M15",
                open_time=datetime.now() - timedelta(minutes=15 * (12 - i)),
                open=151.00,
                high=151.10,
                low=150.90,
                close=151.077, # body size = 0.077
                volume=50,
                is_closed=True
            )
        )
        
    m15_history.append(prev_candle)
    m15_history.append(curr_candle)

    mock_evidence._m15_candles = m15_history

    event_q = queue.Queue()
    config = {
        "realtime_cooldown_seconds": 300,
        "realtime_suppress_asian": False,
        "realtime_log_events": False
    }

    detector = EventDetector(mock_state, mock_evidence, event_q, config)
    detector.set_pip_multiplier(is_jpy=True)

    # Invoke check
    detector._check_candle_pattern_at_level(curr_candle)

    # Assertions
    assert not event_q.empty()
    event = event_q.get()
    
    assert event.event_type == EventType.CANDLE_PATTERN
    assert event.priority == EventPriority.MEDIUM
    assert event.details["pattern"] == "engulfing_bearish"
    assert event.details["at_level"] == 150.00
    assert event.details["timeframe"] == "M15"
    assert event.details["intensity_grade"] == "MEDIUM"
    assert event.details["level_interactions"] == 3


def test_pin_bar_body_color_enforcement():
    # 1. Setup mocks
    mock_state = MagicMock(spec=LiveWorldState)
    mock_state.symbol = "EURUSD=X"
    mock_state.is_initialized = True
    
    mock_inner_state = MagicMock()
    mock_inner_state.session = "LDN"
    mock_inner_state.session_penalty = 1.0
    mock_state._state = mock_inner_state

    mock_evidence = MagicMock(spec=LiveMarketEvidence)
    mock_evidence.key_levels = [1.15000]

    # Create a red (bearish) candle that would otherwise look like a bullish pin bar:
    # Lower shadow is very long (0.75 of range)
    # But it is red (open = 1.15020, close = 1.15010)
    red_candle = LiveCandle(
        timeframe="M15",
        open_time=datetime.now(),
        open=1.15020,
        high=1.15025,
        low=1.14960,
        close=1.15010,
        volume=100,
        is_closed=False
    )
    
    # Create a green (bullish) candle that is a valid bullish pin bar:
    # Lower shadow is very long (0.75 of range)
    # And it is green (open = 1.15010, close = 1.15020)
    green_candle = LiveCandle(
        timeframe="M15",
        open_time=datetime.now(),
        open=1.15010,
        high=1.15025,
        low=1.14960,
        close=1.15020,
        volume=100,
        is_closed=False
    )

    mock_evidence._m15_candles = [red_candle, green_candle]

    event_q = queue.Queue()
    config = {
        "realtime_cooldown_seconds": 300,
        "realtime_suppress_asian": False,
        "realtime_log_events": False
    }

    detector = EventDetector(mock_state, mock_evidence, event_q, config)
    detector.set_pip_multiplier(is_jpy=False)

    # Test 1: Red candle must NOT trigger bullish pin bar
    detector._check_candle_pattern_at_level(red_candle)
    assert event_q.empty(), "Red candle should not trigger a bullish pin bar event"

    # Test 2: Green candle MUST trigger bullish pin bar
    detector._check_candle_pattern_at_level(green_candle)
    assert not event_q.empty(), "Green candle should trigger a bullish pin bar event"
    event = event_q.get()
    assert event.details["pattern"] == "pin_bar_bullish"


def test_backfill_historical_events():
    # 1. Setup mocks
    mock_state = MagicMock(spec=LiveWorldState)
    mock_state.symbol = "EURUSD=X"
    mock_state.is_initialized = True
    
    mock_inner_state = MagicMock()
    mock_inner_state.session = "LDN"
    mock_inner_state.session_penalty = 1.0
    mock_state._state = mock_inner_state

    # Set up mock evidence with historical candles
    mock_evidence = LiveMarketEvidence("EURUSD=X")
    mock_evidence._initialized = True
    mock_evidence._evidence = MagicMock()
    mock_evidence._evidence.key_levels = [1.15000]

    # Create a historical M15 valid bullish pin bar
    pin_candle = LiveCandle(
        timeframe="M15",
        open_time=datetime.now() - timedelta(minutes=30),
        open=1.15010,
        high=1.15025,
        low=1.14960,
        close=1.15020,
        volume=100,
        is_closed=True
    )
    # Additional filler candles to build rolling history (last 10 candles average body)
    mock_evidence._m15_candles.append(pin_candle)
    for i in range(10):
        mock_evidence._m15_candles.append(
            LiveCandle(
                timeframe="M15",
                open_time=datetime.now() - timedelta(minutes=15 * (10 - i)),
                open=1.15000,
                high=1.15005,
                low=1.14995,
                close=1.15002,
                volume=50,
                is_closed=True
            )
        )

    event_q = queue.Queue()
    config = {
        "realtime_cooldown_seconds": 300,
        "realtime_suppress_asian": False,
        "realtime_log_events": False
    }

    # Mock dashboard server and global getter
    from unittest.mock import patch
    mock_dashboard = MagicMock()
    broadcasted_messages = []
    mock_dashboard.broadcast = broadcasted_messages.append

    with patch("axonai.realtime.api_server.get_dashboard", return_value=mock_dashboard):
        detector = EventDetector(mock_state, mock_evidence, event_q, config)
        detector.set_pip_multiplier(is_jpy=False)
        
        # Act
        detector.backfill_historical_events()

    # Assertions
    # 1. No live graph execution was queued
    assert event_q.empty(), "Historical backfill should NOT queue live events"

    # 2. Historical events were broadcasted to dashboard cache
    assert len(broadcasted_messages) > 0, "Historical events should be broadcast to the dashboard"
    
    # 3. Message contains expected details and historical flags
    has_pin_bar = False
    for msg in broadcasted_messages:
        assert msg["historical"] is True
        assert msg["status"] == "skipped"
        assert msg["reason"] == "historical data"
        if msg["event_type"] == "candle_pattern" and msg["details"]["pattern"] == "pin_bar_bullish":
            has_pin_bar = True

    assert has_pin_bar, "Bullish pin bar should be detected and broadcast historically"


def test_sweep_detection():
    # 1. Setup mocks
    mock_state = MagicMock(spec=LiveWorldState)
    mock_state.symbol = "EURUSD=X"
    mock_state.is_initialized = True
    
    mock_inner_state = MagicMock()
    mock_inner_state.session = "LDN"
    mock_inner_state.session_penalty = 1.0
    mock_inner_state.atr_14_h1 = 0.00100 # 10 pips ATR
    mock_state._state = mock_inner_state

    mock_evidence = MagicMock(spec=LiveMarketEvidence)
    # A swing high is located at 1.15000, swing low at 1.14000
    mock_evidence.swing_highs = [{"price": 1.15000}]
    mock_evidence.swing_lows = [{"price": 1.14000}]
    mock_evidence._m15_candles = []
    mock_evidence._level_tracker = MagicMock()

    # A candle that wicks above 1.15000 to 1.15005 (pierces by 5 pips, which is < ATR 10 pips)
    # but closes at 1.14980 (closes back inside)
    candle = LiveCandle(
        timeframe="M15",
        open_time=datetime.now(),
        open=1.14950,
        high=1.15005,
        low=1.14940,
        close=1.14980,
        volume=100,
        is_closed=True
    )

    event_q = queue.Queue()
    config = {
        "realtime_cooldown_seconds": 300,
        "realtime_suppress_asian": False,
        "realtime_log_events": False
    }

    detector = EventDetector(mock_state, mock_evidence, event_q, config)
    detector.set_pip_multiplier(is_jpy=False)

    # Act - trigger check
    detector._check_sweep(candle)
    
    # Confirm the sweep in Phase 2
    from datetime import timedelta
    confirming_candle = LiveCandle(
        timeframe="M15",
        open_time=candle.open_time + timedelta(minutes=15),
        open=1.14980,
        high=1.15000,
        low=1.14850,
        close=1.14900,  # close < open, bearish confirmation
        volume=100,
        is_closed=True
    )
    detector._check_sweep(confirming_candle)

    # Assertions
    assert not event_q.empty()
    event = event_q.get()
    
    assert event.event_type == EventType.SWEEP_DETECTED
    assert event.priority == EventPriority.HIGH
    assert event.details["swept_level"] == 1.15000
    assert event.details["direction"] == "bearish_sweep"
    assert event.details["pierce_pips"] == pytest.approx(0.00005 / 0.0001)



