"""LLM bridge for high-value decision making."""

import time
import json
import logging
from dataclasses import dataclass
from typing import Optional
from anthropic import Anthropic

from market_state import StateTransition
from market_context import MarketContext

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class LLMDecision:
    """The structured decision produced by the LLM."""
    action: str
    confidence: float
    reasoning: str
    invalidation: str
    max_risk_pips: int
    timestamp_ms: int
    context_snapshot: MarketContext

class LLMBridge:
    """Coordinates conditional queries to Anthropic's Claude model based on market context."""
    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022", debug: bool = False):
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.debug = debug
        self.last_query_time = 0.0

    def should_query(self, transition: StateTransition, context: MarketContext) -> bool:
        """Determines if the state transition warrants an LLM decision."""
        # 1. Transition involves SWEEPING, REVERSING, or EXHAUSTING
        states_of_interest = {"SWEEPING", "REVERSING", "EXHAUSTING"}
        involves_target_state = (transition.from_state in states_of_interest or transition.to_state in states_of_interest)
        if not involves_target_state:
            return False

        # 2. Spread safe check
        if not context.spread_safe:
            return False

        # 3. Confirmed signal OR state confidence > 0.75
        if not (context.confirmed_signal or context.state_confidence > 0.75):
            return False

        # 4. Rate limit check (> 60 seconds ago)
        now = time.time()
        if now - self.last_query_time < 60.0:
            return False

        return True

    def query(self, transition: StateTransition, context: MarketContext) -> LLMDecision:
        """Query the LLM to get a structured decision."""
        self.last_query_time = time.time()

        # Handle Mock Mode
        if not self.client.api_key or self.client.api_key == "mock_key":
            if self.debug:
                print("[LLMBridge Mock] Simulating LLM query for transition...")
            action = "long" if "SWEEP" in transition.to_state or "REVERSING" in transition.to_state else "wait"
            return LLMDecision(
                action=action,
                confidence=0.85 if action != "wait" else 0.0,
                reasoning=f"Mocked behavioral explanation for {transition.from_state} -> {transition.to_state}.",
                invalidation="Price action invalidating the support levels.",
                max_risk_pips=15,
                timestamp_ms=int(time.time() * 1000),
                context_snapshot=context
            )

        system_prompt = (
            "You are a professional forex trading analyst specializing in EURUSD and GBPUSD microstructure.\n"
            "You receive a compressed market state snapshot. You must reason about it and return a structured trading decision.\n"
            "You think like an institutional trader: you care about liquidity, aggression, absorption, and behavioral transitions — not indicators.\n"
            "You are conservative. You only recommend action when the evidence is unusually clear.\n"
            "You always output valid JSON and nothing else."
        )

        user_prompt = f"""Market state transition detected.

Symbol: {context.symbol}
Session: {context.session}
Transition: {transition.from_state} → {transition.to_state} (confidence: {transition.confidence:.2f})
State duration before transition: {context.state_duration_sec:.1f}s

Microstructure:
- Dominant side: {context.dominant_side}
- Buy pressure: {context.buy_pressure:.2f} | Sell pressure: {context.sell_pressure:.2f}
- Pressure delta: {context.pressure_delta:+.3f} (negative = buyers weakening)
- Imbalance score: {context.imbalance_score:.2f}/10
- Absorption detected: {context.absorption_detected}
- Aggression shift: {context.aggression_shift}
- Velocity collapsing: {context.velocity_collapsing}

Structure:
- Sweep detected: {context.sweep_detected}
- Continuation failed: {context.continuation_failed}
- Structure break: {context.structure_break}
- Retest active: {context.retest_active}

Risk environment:
- Spread: {context.spread_pips:.1f} pips (safe: {context.spread_safe})
- Volatility expanding: {context.volatility_expanding}

Pre-confirmed signal: {context.confirmed_signal} | Direction: {context.signal_direction} | Confidence: {context.signal_confidence:.2f}

Reason about this transition. What is the market doing? Is this a tradeable setup?
Return JSON only:
{{
  "action": "long" | "short" | "wait",
  "confidence": 0.0–1.0,
  "reasoning": "2-3 sentence behavioral explanation",
  "invalidation": "what would make this setup wrong",
  "max_risk_pips": integer
}}"""

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt}
                ]
            )
            response_text = message.content[0].text.strip()
            
            if self.debug:
                print(f"LLM Raw Response: {response_text}")

            parsed = json.loads(response_text)
            
            return LLMDecision(
                action=parsed.get("action", "wait"),
                confidence=float(parsed.get("confidence", 0.0)),
                reasoning=parsed.get("reasoning", "No explanation provided."),
                invalidation=parsed.get("invalidation", "No invalidation defined."),
                max_risk_pips=int(parsed.get("max_risk_pips", 10)),
                timestamp_ms=int(time.time() * 1000),
                context_snapshot=context
            )
        except Exception as e:
            logger.error("LLMBridge query or parse failure: %s", e)
            return LLMDecision(
                action="wait",
                confidence=0.0,
                reasoning="parse_error",
                invalidation="n/a",
                max_risk_pips=10,
                timestamp_ms=int(time.time() * 1000),
                context_snapshot=context
            )
