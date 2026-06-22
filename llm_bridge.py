"""LLM bridge for high-value decision making."""

import time
import json
import logging
from dataclasses import dataclass
from typing import Optional
from openai import OpenAI

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
    """Coordinates conditional queries to DeepSeek model based on market context."""
    def __init__(self, api_key: str, model: str = "deepseek-chat", debug: bool = False):
        self.client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        self.model = model
        self.debug = debug
        self.last_query_time = 0.0
        self._last_context_key: tuple = ()
        self.paused = False

    def should_query(self, transition: StateTransition, context: MarketContext) -> tuple[bool, dict]:
        """Determines if the state transition warrants an LLM decision."""
        gate_status = {
            "state_passed": False,
            "spread_passed": False,
            "conviction_passed": False,
            "rate_limit_passed": False,
            "context_passed": False,
            "llm_paused": self.paused
        }

        if self.paused:
            return False, gate_status

        # 1. Transition involves SWEEPING, REVERSING, or EXHAUSTING
        states_of_interest = {"SWEEPING", "REVERSING", "EXHAUSTING"}
        involves_target_state = (transition.from_state in states_of_interest or transition.to_state in states_of_interest)
        gate_status["state_passed"] = involves_target_state
        if not involves_target_state:
            return False, gate_status

        # 2. Spread safe check
        gate_status["spread_passed"] = context.spread_safe
        if not context.spread_safe:
            return False, gate_status

        # 3. Confirmed signal OR state confidence > 0.75
        has_conviction = bool(context.confirmed_signal or context.state_confidence > 0.75)
        gate_status["conviction_passed"] = has_conviction
        if not has_conviction:
            return False, gate_status

        # 4. Rate limit check (> 60 seconds ago)
        now = time.time()
        rate_safe = (now - self.last_query_time >= 60.0)
        gate_status["rate_limit_passed"] = rate_safe
        if not rate_safe:
            return False, gate_status

        # 5. Context-change guard
        context_key = (context.symbol, transition.to_state, context.dominant_side)
        context_changed = (context_key != self._last_context_key)
        gate_status["context_passed"] = context_changed
        if not context_changed:
            return False, gate_status

        return True, gate_status

    def query(self, transition: StateTransition, context: MarketContext) -> LLMDecision:
        """Query the LLM to get a structured decision."""
        self.last_query_time = time.time()

        # Update context-change key
        context_key = (context.symbol, transition.to_state, context.dominant_side)
        self._last_context_key = context_key

        # Handle Mock Mode (enforce same rate limit for log integrity)
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

Peak detector:
- Divergence warning: {context.peak_divergence_warning}
- Peak confirmed: {context.peak_confirmed}
- Peak confidence: {context.peak_confidence:.2f}
- Dominant side at peak: {context.peak_dominant_side}

Macro consensus (System 2):
- Bias: {context.macro_bias}
- Confidence: {context.macro_confidence:.2f}
- Key level: {context.macro_key_level:.5f}
- Last updated: {context.macro_bias_age_sec:.0f}s ago

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
            response = self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            response_text = response.choices[0].message.content.strip()
            
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
