import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from axonai.realtime.event_types import MarketEvent, EventType
from axonai.realtime.live_state import LiveWorldState, LiveMarketEvidence

logger = logging.getLogger(__name__)

class MarketContextEngine:
    """Combines HTF trend, MTF pressure, and LTF triggers to classify market state."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def classify_state(
        self,
        state: LiveWorldState,
        evidence: LiveMarketEvidence,
        event: MarketEvent
    ) -> str:
        """Classify current market state based on multi-timeframe snaps and current event."""
        # 1. Gather HTF and MTF trends
        h4_trend = getattr(evidence._evidence, "trend_direction_h4", "sideways")
        h1_trend = getattr(evidence._evidence, "trend_direction_h1", "sideways")
        
        # 2. Gather regime scores and dominant regime from world state
        ws_state = state._state
        dominant_regime = getattr(ws_state, "dominant_regime", "ranging")
        regime_scores = getattr(ws_state, "regime_scores", {})
        
        # 3. Analyze trigger event characteristics
        event_type = event.event_type
        details = event.details if hasattr(event, "details") else {}
        
        # 4. Classification Logic
        # A. BREAKOUT: high breakout score or level breach with alignment
        if dominant_regime == "breakout" or event_type == EventType.LEVEL_BREACH:
            return "BREAKOUT"

        # B. EXHAUSTION: High velocity with low displacement (price efficiency collapse)
        if event_type == EventType.PEAK_DETECTION:
            confidence = details.get("peak_confidence", 0.0)
            confirmed = details.get("peak_confirmed", False)
            efficiency = details.get("price_per_tick_efficiency", 1.0)
            
            # High divergence / Z-score but low efficiency (absorption/exhaustion)
            if confirmed and efficiency < 0.15:
                return "EXHAUSTION"

        # C. REVERSAL: Liquidity sweeps or peak rejections with opposing flow gain
        if event_type == EventType.SWEEP_DETECTED:
            return "REVERSAL"
            
        if event_type == EventType.PEAK_DETECTION:
            peak_type = details.get("peak_type", "")
            opposing_flow = details.get("velocity_divergence", 0.0)
            if peak_type in ("velocity_exhaustion", "microstructure_exhaustion") or opposing_flow > 0.8:
                return "REVERSAL"

        # D. PULLBACK: Opposing H1 move within strong H4 trend
        if h4_trend != "sideways" and h1_trend != h4_trend:
            return "PULLBACK"

        # E. TREND_CONTINUATION: H4 and H1 aligned, with trend-supporting events
        if h4_trend != "sideways" and h1_trend == h4_trend:
            return "TREND_CONTINUATION"

        # F. RANGE_NOISE: High ranging score/compression
        if dominant_regime in ("ranging", "compression"):
            return "RANGE_NOISE"

        return "UNKNOWN"


class MarketStateMachine:
    """Tracks state transitions over time and computes transition confidence."""

    def __init__(self, history_size: int = 10):
        self.state_history: List[str] = ["UNKNOWN"]
        self.history_size = history_size

    def update_state(self, new_state: str, regime_confidence: float) -> float:
        """Append state to history and return confidence score of the transition."""
        prev_state = self.state_history[-1]
        if new_state != prev_state:
            self.state_history.append(new_state)
            if len(self.state_history) > self.history_size:
                self.state_history.pop(0)
                
            logger.info("MarketStateMachine: Transition detected: %s -> %s", prev_state, new_state)

        # Transition confidence matches the current context classification confidence
        return max(0.1, min(1.0, regime_confidence))

    def get_transition_path(self) -> List[str]:
        return list(self.state_history)


class ExecutionDecisionLayer:
    """Translates setups and state transitions into trading execution decisions with explainability."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.context_engine = MarketContextEngine(config)
        self.state_machine = MarketStateMachine()

    def evaluate(
        self,
        state: LiveWorldState,
        evidence: LiveMarketEvidence,
        event: MarketEvent
    ) -> Dict[str, Any]:
        """Process event, classify state, and determine trade execution."""
        details = event.details if hasattr(event, "details") else {}
        peak_type = details.get("peak_type", "")
        
        # Core filter: restrict to microstructure peak reversals by default
        is_peak = event.event_type == EventType.PEAK_DETECTION
        is_exhaustion = peak_type in ("velocity_exhaustion", "microstructure_exhaustion")
        
        allow_non_peaks = self.config.get("allow_non_peaks", False)
        
        if not allow_non_peaks and not (is_peak and is_exhaustion):
            return {
                "trade_allowed": False,
                "direction": None,
                "confidence": 0.0,
                "reason": "Signal is not a verified microstructure peak exhaustion.",
                "explainability": {
                    "market_state": "UNKNOWN",
                    "why_trade": "Rejected: Strategy restricted to microstructure peaks.",
                    "why_not_wait": "Waiting for high-conviction peak exhaustion.",
                    "supporting_factors": [],
                    "risk_factors": ["Event is not a microstructure peak"]
                }
            }

        # 1. Determine direction
        direction = None
        dir_str = details.get("direction", "")
        if "bullish" in dir_str or "low" in peak_type or "support" in dir_str:
            direction = "BUY"
        elif "bearish" in dir_str or "high" in peak_type or "resistance" in dir_str:
            direction = "SELL"
            
        # 2. Context & State Machine
        market_state = self.context_engine.classify_state(state, evidence, event)
        ws_state = state._state
        regime_conf = getattr(ws_state, "regime_confidence", 0.5)
        transition_conf = self.state_machine.update_state(market_state, regime_conf)

        # 3. Peak Intensity & Quality Validation
        confidence = details.get("peak_confidence", 0.0)
        confirmed = details.get("peak_confirmed", False)
        intensity = details.get("intensity", "MEDIUM")
        
        trade_allowed = True
        reason = f"Confirmed {market_state} signal triggered."
        supporting_factors = []
        risk_factors = []

        # ── Intensity Gate ──
        if intensity not in ("HIGH", "MEDIUM"):
            trade_allowed = False
            reason = f"Peak intensity too low: {intensity}"
            risk_factors.append(reason)
            
        if intensity == "HIGH" and not confirmed and confidence < 0.6:
            trade_allowed = False
            reason = f"High intensity peak lacks confirmation (confidence={confidence:.2f})"
            risk_factors.append(reason)

        # ── Quality Scoring ──
        if intensity == "MEDIUM":
            sc = details.get("swing_confidence", None)
            signal_quality = 0.5 + 0.3 * sc if sc is not None else 0.65
        else:
            signal_quality = 0.3 + confidence * 0.5
            
        if confirmed:
            signal_quality += 0.2

        # Quality Floor Gate (0.65)
        if signal_quality < 0.65:
            trade_allowed = False
            reason = f"Signal quality ({signal_quality:.2f}) below minimum floor of 0.65"
            risk_factors.append(reason)

        # ── S/R Proximity check ──
        pip_mult = getattr(evidence, "_pip_mult", 0.0001)
        if direction is not None and self.config.get("require_sr_proximity", True):
            active_levels = [l for l in evidence.price_levels if l.is_active]
            closest_dist = float("inf")
            closest_lvl = None
            for lvl in active_levels:
                dist_pips = abs(event.price - lvl.price) / pip_mult
                if dist_pips < closest_dist:
                    closest_dist = dist_pips
                    closest_lvl = lvl
            
            if closest_lvl is None or closest_dist > 5.0:
                trade_allowed = False
                reason = f"Trade price is outside S/R proximity window (closest: {closest_dist:.2f} pips)."
                risk_factors.append(reason)
            else:
                supporting_factors.append(f"Near {closest_lvl.level_type} zone: {closest_dist:.2f} pips")

        # ── Daily Trend Alignment Check (replaces hard daily blocks with signal quality adjustments) ──
        daily_trend = getattr(evidence, "trend_direction_h4", "sideways")
        if direction is not None and daily_trend != "sideways":
            if (daily_trend == "up" and direction != "BUY") or (daily_trend == "down" and direction != "SELL"):
                # Counter-trend peak: reduce signal quality / confidence score
                signal_quality -= 0.15
                risk_factors.append(f"Counter-trend trade against H4 Trend ({daily_trend})")
            else:
                supporting_factors.append(f"Aligned with H4 Trend ({daily_trend})")

        # ── Spread check ──
        spread_pips = getattr(ws_state, "spread_pips", 1.0)
        spread_safe = getattr(ws_state, "spread_safe", True)
        if not spread_safe:
            trade_allowed = False
            reason = f"Trade rejected: spread is too wide ({spread_pips:.1f} pips)."
            risk_factors.append(f"Wide spread: {spread_pips:.1f} pips")

        # Set final outputs
        confidence_score = max(0.0, min(1.0, signal_quality))

        # Explainability Payload
        why_trade = reason if trade_allowed else "No trade Setup allowed."
        why_not_wait = (
            "Exhaustion/Reversal pattern has pinpointed the peak; waiting increases SL distance."
            if market_state in ("REVERSAL", "EXHAUSTION") and trade_allowed
            else "Breakout candle momentum is active; entering now to avoid slippage."
            if market_state == "BREAKOUT" and trade_allowed
            else "Waiting for clearer structural reversal or pullback confirmation."
        )

        explainability = {
            "market_state": market_state,
            "why_trade": why_trade,
            "why_not_wait": why_not_wait,
            "supporting_factors": supporting_factors,
            "risk_factors": risk_factors
        }

        return {
            "trade_allowed": trade_allowed,
            "direction": direction if trade_allowed else None,
            "confidence": round(confidence_score, 2),
            "reason": reason,
            "explainability": explainability
        }
