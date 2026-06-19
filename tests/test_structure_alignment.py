import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from axonai.realtime.event_types import LiveCandle, MarketEvent, EventType
from axonai.realtime.live_state import LiveMarketEvidence, LiveWorldState
from axonai.realtime.decision_intelligence import ExecutionDecisionLayer

BASE_TIME = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)

def test_get_empirical_metrics_and_patterns():
    evidence = LiveMarketEvidence("EURUSD=X")
    evidence._initialized = True

    # Seed 14 normal candles to build baseline stats
    # Baseline: body size = 0.00010 (1.0 pip), wick size = 0.00005 (0.5 pip), volume = 100
    for i in range(14):
        evidence._m15_candles.append(
            LiveCandle(
                timeframe="M15",
                open_time=BASE_TIME - timedelta(minutes=15 * (15 - i)),
                open=1.15000,
                high=1.15015,
                low=1.14995,
                close=1.15010,
                volume=100,
                is_closed=True
            )
        )

    # 1. Test Wick Climax
    # Current wick: upper shadow (1.15010 to 1.15050) + lower shadow (1.14990 to 1.15000) = 4.0 + 1.0 = 5.0 pips (0.00050)
    # This is far above the average wick of 0.5 pips.
    wick_climax_candle = LiveCandle(
        timeframe="M15",
        open_time=BASE_TIME,
        open=1.15000,
        high=1.15050,
        low=1.14990,
        close=1.15010,
        volume=100,
        is_closed=True
    )
    evidence._m15_candles.append(wick_climax_candle)
    patterns = evidence.detect_empirical_reversal_patterns("M15")
    assert patterns["wick_climax"] is True
    assert patterns["volume_stall"] is False
    assert patterns["v_rebound"] is False

    # Clean up latest candle
    evidence._m15_candles.pop()

    # 2. Test Volume Stall (Absorption)
    # Current body: 0.00002 (0.2 pips) vs baseline 1.0 pip (very small body)
    # Current volume: 200 vs baseline 100 (high volume)
    stall_candle = LiveCandle(
        timeframe="M15",
        open_time=BASE_TIME,
        open=1.15000,
        high=1.15005,
        low=1.14995,
        close=1.15002,
        volume=200,
        is_closed=True
    )
    evidence._m15_candles.append(stall_candle)
    patterns = evidence.detect_empirical_reversal_patterns("M15")
    assert patterns["wick_climax"] is False
    assert patterns["volume_stall"] is True
    assert patterns["v_rebound"] is False

    # Clean up
    evidence._m15_candles.pop()

    # 3. Test V-Rebound
    # We need 3 bearish candles, followed by a large bullish candle
    # Prior 3 candles (indices -4, -3, -2): all bearish (close < open)
    evidence._m15_candles.clear()
    for i in range(11):
        evidence._m15_candles.append(
            LiveCandle(
                timeframe="M15",
                open_time=BASE_TIME - timedelta(minutes=15 * (15 - i)),
                open=1.15000,
                high=1.15015,
                low=1.14995,
                close=1.15010,
                volume=100,
                is_closed=True
            )
        )
    # Bearish candles
    for i in range(3):
        evidence._m15_candles.append(
            LiveCandle(
                timeframe="M15",
                open_time=BASE_TIME - timedelta(minutes=15 * (4 - i)),
                open=1.15050,
                high=1.15060,
                low=1.15000,
                close=1.15010, # bearish (4.0 pips body)
                volume=100,
                is_closed=True
            )
        )
    # Aggressive bullish candle (6.0 pips body vs average body of ~1.8 pips)
    bullish_candle = LiveCandle(
        timeframe="M15",
        open_time=BASE_TIME,
        open=1.15000,
        high=1.15070,
        low=1.14990,
        close=1.15060,
        volume=100,
        is_closed=True
    )
    evidence._m15_candles.append(bullish_candle)
    patterns = evidence.detect_empirical_reversal_patterns("M15")
    assert patterns["v_rebound"] is True


def test_execution_decision_structural_alignment():
    # Setup decision layer with require_structural_alignment = True
    config = {
        "require_structural_alignment": True,
        "require_sr_proximity": False  # isolate testing to structural alignment
    }
    layer = ExecutionDecisionLayer(config)

    mock_state = MagicMock(spec=LiveWorldState)
    mock_state._state = MagicMock()
    mock_state._state.session = "london"
    mock_state._state.regime_confidence = 0.8
    mock_state._state.spread_safe = True
    mock_state._state.spread_pips = 1.0
    
    mock_evidence = MagicMock(spec=LiveMarketEvidence)
    mock_evidence._evidence = MagicMock()
    mock_evidence.trend_direction_h4 = "sideways"

    # Event trigger
    event = MarketEvent(
        event_type=EventType.PEAK_DETECTION,
        priority=3,
        timestamp=BASE_TIME,
        symbol="EURUSD=X",
        price=1.15000,
        details={
            "peak_type": "microstructure_exhaustion",
            "direction": "bullish_reversal",
            "peak_confidence": 0.8,
            "peak_confirmed": True,
            "intensity": "HIGH"
        }
    )

    # Scenario A: No empirical patterns present on M15/H1 -> Should block trade
    mock_evidence.detect_empirical_reversal_patterns.return_value = {
        "wick_climax": False,
        "volume_stall": False,
        "v_rebound": False
    }
    decision = layer.evaluate(mock_state, mock_evidence, event)
    assert decision["trade_allowed"] is False
    assert "Blocked: No empirical reversal structure" in decision["reason"]

    # Scenario B: Empirical pattern (wick_climax) is active -> Should allow trade
    mock_evidence.detect_empirical_reversal_patterns.side_effect = lambda tf: (
        {"wick_climax": True, "volume_stall": False, "v_rebound": False} if tf == "M15"
        else {"wick_climax": False, "volume_stall": False, "v_rebound": False}
    )
    decision = layer.evaluate(mock_state, mock_evidence, event)
    assert decision["trade_allowed"] is True
    assert "M15 wick_climax" in decision["explainability"]["supporting_factors"][-1]


def test_directional_empirical_patterns():
    evidence = LiveMarketEvidence("EURUSD=X")
    evidence._initialized = True

    # Seed 14 normal candles to build baseline stats
    for i in range(14):
        evidence._m15_candles.append(
            LiveCandle(
                timeframe="M15",
                open_time=BASE_TIME - timedelta(minutes=15 * (15 - i)),
                open=1.15000,
                high=1.150075,
                low=1.149925,
                close=1.15000, # body = 0, upper wick = 0.75 pips, lower wick = 0.75 pips
                volume=100,
                is_closed=True
            )
        )

    # 1. Bearish rejection (huge upper wick, no lower wick)
    # Upper wick: 1.15000 to 1.15050 = 5.0 pips. Lower wick = 0.
    bearish_climax_candle = LiveCandle(
        timeframe="M15",
        open_time=BASE_TIME,
        open=1.15000,
        high=1.15050,
        low=1.15000,
        close=1.15000,
        volume=100,
        is_closed=True
    )
    evidence._m15_candles.append(bearish_climax_candle)

    # If direction is bearish_reversal, it should be a wick climax
    pats_bearish = evidence.detect_empirical_reversal_patterns("M15", direction="bearish_reversal")
    assert pats_bearish["wick_climax"] is True

    # If direction is bullish_reversal, it should NOT be a wick climax (since lower wick is 0)
    pats_bullish = evidence.detect_empirical_reversal_patterns("M15", direction="bullish_reversal")
    assert pats_bullish["wick_climax"] is False

    evidence._m15_candles.pop()

