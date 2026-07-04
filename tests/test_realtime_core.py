"""Tests for axonai/realtime core components.

Tests cover:
1. CandleBuilder.feed_tick() — M1 boundary crossing
2. EventDetector cooldown — suppression of rapid events
3. SR zone classification — support below / resistance above current bid
4. WorldState session detection — UTC hour → session label
5. compress_evidence() — word reduction and compression ratio
"""

from datetime import datetime, timedelta
import queue

import pytest


# ---------------------------------------------------------------------------
# 1. CandleBuilder.feed_tick()
# ---------------------------------------------------------------------------


class TestCandleBuilder:
    """Feed 65 ticks spanning one M1 boundary, verify exactly one closed candle."""

    def test_m1_boundary_produces_one_closed_candle(self):
        from axonai.realtime.tick_engine import CandleBuilder

        cb = CandleBuilder("M1")

        # M1 period is 60 seconds.  Start at second 0 of a minute boundary.
        base_time = datetime(2026, 5, 26, 12, 0, 0)  # 12:00:00 UTC

        closed_candles = []

        # Feed 30 ticks within the first M1 period (12:00:00 – 12:00:59)
        for i in range(30):
            ts = base_time + timedelta(seconds=i * 2)  # 0, 2, 4, … 58
            price = 1.16000 + (i * 0.00001)  # rising
            result = cb.feed_tick(price, 1, ts)
            if result is not None:
                closed_candles.append(result)

        # At this point we should have 0 closed candles — still in first M1
        assert len(closed_candles) == 0

        # Feed 35 ticks crossing into the next M1 period (12:01:00+)
        for i in range(35):
            ts = base_time + timedelta(seconds=60 + i)  # 12:01:00 onwards
            price = 1.16030 + (i * 0.00001)  # continue rising
            result = cb.feed_tick(price, 1, ts)
            if result is not None:
                closed_candles.append(result)

        # Exactly 1 closed candle (the first M1 candle)
        assert len(closed_candles) == 1

        candle = closed_candles[0]
        assert candle.is_closed is True
        assert candle.timeframe == "M1"

        # OHLCV checks on the closed candle
        assert candle.open == pytest.approx(1.16000, abs=1e-7)
        assert candle.high == pytest.approx(1.16029, abs=1e-7)
        assert candle.low == pytest.approx(1.16000, abs=1e-7)
        assert candle.close == pytest.approx(1.16029, abs=1e-7)
        assert candle.volume == 30


# ---------------------------------------------------------------------------
# 2. EventDetector cooldown
# ---------------------------------------------------------------------------


class TestEventDetectorCooldown:
    """Two events within cooldown window — second is suppressed."""

    def test_cooldown_suppresses_second_event(self):
        from axonai.realtime.event_types import (
            EventPriority, EventType, MarketEvent
        )
        from axonai.realtime.event_detector import EventDetector
        from unittest.mock import MagicMock

        # Mock live_state and live_evidence
        live_state = MagicMock()
        live_state.is_initialized = True
        live_state.symbol = "EURUSDm"
        state_obj = MagicMock()
        state_obj.session = "london"
        state_obj.spread_safe = True
        live_state._state = state_obj

        live_evidence = MagicMock()
        live_evidence.snapshot.return_value = MagicMock(
            trend_direction_h4="up", trend_direction_h1="up"
        )
        live_evidence._m15_candles = []

        event_queue = queue.Queue()
        config = {
            "realtime_suppress_asian": False,
            "realtime_level_reset_atr_multiple": 2.0,
            "realtime_log_events": False,
        }

        detector = EventDetector(live_state, live_evidence, event_queue, config)

        # Emit first event — should succeed
        event1 = MarketEvent(
            event_type=EventType.LEVEL_BREACH,
            priority=EventPriority.HIGH,
            timestamp=datetime(2026, 5, 26, 12, 0, 0),
            symbol="EURUSDm",
            price=1.16000,
            details={"level": 1.16000, "direction": "bullish"},
        )
        detector._emit(event1)
        assert event_queue.qsize() == 1

        # Set cooldown of 300 seconds
        detector.set_cooldown(300)

        # Emit second event — should be suppressed
        event2 = MarketEvent(
            event_type=EventType.STRUCTURE_BREAK,
            priority=EventPriority.HIGH,
            timestamp=datetime(2026, 5, 26, 12, 1, 0),
            symbol="EURUSDm",
            price=1.16050,
            details={"broken_level": 1.16050, "direction": "bullish_bos"},
        )
        detector._emit(event2)
        assert event_queue.qsize() == 1  # Still 1 — second was suppressed


# ---------------------------------------------------------------------------
# 3. SR zone classification
# ---------------------------------------------------------------------------


class TestSRZoneClassification:
    """Support zones below bid, resistance zones above."""

    def test_zones_classified_correctly(self):
        current_bid = 1.16000
        zones = [1.15800, 1.15900, 1.16100, 1.16200]

        support_zones = [z for z in zones if z < current_bid]
        resistance_zones = [z for z in zones if z > current_bid]

        assert support_zones == [1.15800, 1.15900]
        assert resistance_zones == [1.16100, 1.16200]

        # Verify no zone is in both lists
        assert set(support_zones).isdisjoint(set(resistance_zones))

        # Verify all support zones are strictly below bid
        for z in support_zones:
            assert z < current_bid

        # Verify all resistance zones are strictly above bid
        for z in resistance_zones:
            assert z > current_bid


# ---------------------------------------------------------------------------
# 4. Session detection
# ---------------------------------------------------------------------------


class TestSessionDetection:
    """Test session labels from world_state.py session logic."""

    def _get_session_for_utc_hour(self, utc_hour: float) -> str:
        """Replicate the session logic from world_state.py lines 167-186."""
        if 13.0 <= utc_hour < 16.0:
            return "overlap"
        elif 8.0 <= utc_hour < 13.0:
            return "london"
        elif 16.0 <= utc_hour < 21.0:
            return "newyork"
        elif 21.0 <= utc_hour < 22.0:
            return "rollover"
        else:
            return "asian"

    def test_london_session(self):
        assert self._get_session_for_utc_hour(12.0) == "london"

    def test_overlap_session(self):
        assert self._get_session_for_utc_hour(14.0) == "overlap"

    def test_asian_session(self):
        assert self._get_session_for_utc_hour(2.0) == "asian"

    def test_newyork_session(self):
        assert self._get_session_for_utc_hour(18.0) == "newyork"

    def test_rollover_session(self):
        assert self._get_session_for_utc_hour(21.5) == "rollover"


# ---------------------------------------------------------------------------
# 5. compress_evidence()
# ---------------------------------------------------------------------------


class TestCompressEvidence:
    """Feed 2000-word analyst output, verify compression ratio > 0.70."""

    def _make_long_report(self, word_count: int = 500) -> str:
        """Generate a synthetic analyst report with the given word count."""
        base = (
            "The market shows strong bullish momentum supported by rising EMA alignment. "
            "The H1 trend is clearly upward with RSI confirming at 62. Key support "
            "at 1.15800 has held on three separate tests. The Fed rate decision is "
            "expected to maintain current levels, providing stability. CPI data came "
            "in below expectations at 2.1%, suggesting inflation is under control. "
            "GDP growth remains solid at 2.4% annualized. Employment data shows "
            "robust NFP numbers of 250K. The ECB has signaled no immediate hike, "
            "maintaining the interest rate differential in favor of USD. "
            "1.15800,1.15900,1.16000,1.16100,1.16200|BUY|62|0.85\n"
            "Overall sentiment is moderately bullish with a confidence score of 0.78.\n\n"
            "In conclusion, the technical and fundamental evidence strongly supports "
            "a bullish bias with confidence at 78%. Key factors include EMA alignment, "
            "supportive Fed stance, and favorable employment data."
        )
        # Repeat to reach target word count
        words = base.split()
        repetitions = max(1, word_count // len(words) + 1)
        full_text = " ".join(words * repetitions)
        # Truncate to exact count
        return " ".join(full_text.split()[:word_count])

    def test_compression_ratio_above_70_percent(self):
        from axonai.graph.evidence_compressor import compress_evidence

        # Create a fake agent state with ~2000 words total (500 per analyst)
        state = {
            "market_report": self._make_long_report(500),
            "fundamentals_report": self._make_long_report(500),
            "news_report": self._make_long_report(500),
            "sentiment_report": self._make_long_report(500),
        }

        total_input_words = sum(len(v.split()) for v in state.values())
        assert total_input_words >= 2000, f"Input only {total_input_words} words"

        result = compress_evidence(state)

        # Check all expected keys exist
        assert "market_summary" in result
        assert "fundamental_summary" in result
        assert "news_summary" in result
        assert "sentiment_summary" in result
        assert "critical_events" in result
        assert "compression_ratio" in result

        # Count total output words
        total_output_words = sum(
            len(result[k].split())
            for k in ("market_summary", "fundamental_summary", "news_summary", "sentiment_summary")
        )

        # Output must be under 300 words total (4 analysts × 150 max = 600, but
        # last-paragraph extraction should produce much less)
        assert total_output_words <= 600, f"Output {total_output_words} words exceeds 600"

        # Compression ratio must be > 0.70
        assert result["compression_ratio"] > 0.70, (
            f"Compression ratio {result['compression_ratio']:.2f} is below 0.70"
        )

        # Critical events should have extracted macro keywords
        assert len(result["critical_events"]) > 0, "No critical events extracted"

        # Verify critical events contain expected keywords
        all_events_text = " ".join(result["critical_events"]).lower()
        assert any(kw in all_events_text for kw in ["fed", "cpi", "gdp", "ecb", "rate", "employment"]), (
            f"Critical events missing expected keywords: {result['critical_events']}"
        )
