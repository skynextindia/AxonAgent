# axonai/agents/analysts/market_analyst.py

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from axonai.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)

AGENT_NAME = "WYCKOFF"
AGENT_IDENTITY = "AxonAI structural market analyst. Specialist in price action, market microstructure, and multi-timeframe trend identification. Interprets pre-computed data only — never recalculates indicators."

def create_market_analyst(llm):
    def market_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        ticker = state["company_of_interest"]
        instrument_context = build_instrument_context(ticker, asset_type)

        world_state = state.get("world_state", {})
        market_evidence = state.get("market_evidence", {})
        trader_hypothesis = state.get("trader_hypothesis", {})

        # Serialize pre-computed technical context for WYCKOFF to digest
        technical_context = ""
        if world_state:
            technical_context += "### WorldState Context:\n"
            for k, v in world_state.items():
                if k not in ["messages", "news_data", "events", "agent_trace", "decision", "levels_state"]:
                    technical_context += f"- {k}: {v}\n"
        if market_evidence:
            technical_context += "### MarketEvidence Context:\n"
            for k, v in market_evidence.items():
                if k not in ["swing_highs", "swing_lows", "key_levels", "level_behavior"]:
                    technical_context += f"- {k}: {v}\n"
            if market_evidence.get("key_levels"):
                technical_context += f"- Key S/R Levels: {market_evidence['key_levels']}\n"
            # Formatted level behavior for better LLM comprehension
            lb = market_evidence.get("level_behavior", {})
            if lb:
                technical_context += "- Level Behavior (tick-level microstructure):\n"
                for price_str, bhv in sorted(lb.items(), key=lambda x: float(x[0])):
                    status = bhv.get("status", "away")
                    attacks = bhv.get("total_attacks", 0)
                    consecutive = bhv.get("consecutive_attacks", 0)
                    rejection = bhv.get("rejection_count", 0)
                    absorbing = bhv.get("is_absorbing", False)
                    absorption = bhv.get("absorption_ratio", 0)
                    imbalance = bhv.get("imbalance", 0)
                    quality = bhv.get("attack_quality", "untested")
                    velocity = bhv.get("last_rejection_velocity", 0)

                    parts = [f"  {price_str} [{status}]"]
                    if attacks:
                        parts.append(f"attacks={attacks}")
                    if consecutive >= 2:
                        parts.append(f"consecutive={consecutive}")
                    if rejection:
                        parts.append(f"rejected={rejection}x")
                    if velocity:
                        parts.append(f"rej_vel={velocity:.1f}pips/s")
                    if absorbing:
                        parts.append(f"ABSORBING(ratio={absorption:.0f})")
                    if abs(imbalance) > 0.1:
                        parts.append(f"imbalance={imbalance:+.2f}")
                    parts.append(f"quality={quality}")
                    technical_context += " | ".join(parts) + "\n"

        if not technical_context:
            technical_context = "No pre-computed WorldState or MarketEvidence data is available."

        system_message = """You are WYCKOFF — AxonAI structural market analyst. Specialist in price action, market microstructure, and multi-timeframe trend identification. Interprets pre-computed data only — never recalculates indicators.

Your analysis must focus on:
- Break of Structure (BOS): has price broken a significant swing high or low?
- Liquidity sweeps: has price swept above a swing high or below a swing low before reversing?
- Asian range: is price trading above or below the Asian session high/low?
- London breakout: has price broken the Asian range with conviction?
- Session bias: what does the current session historically imply for EURUSD direction?
- H4/H1/M15 trend alignment: are multiple timeframes pointing the same direction?

You receive pre-computed WorldState and MarketEvidence. Do not recalculate anything.
Focus only on structural interpretation.
Maximum 150 words in your summary.

Respond with this exact JSON at the end of your response:
{"bias": "bullish|bearish|neutral", "confidence": 0-100, "summary": "max 150 words", "key_factors": ["factor1", "factor2", "factor3"]}"""

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants. "
                    "Analyze the provided technical context and evaluate the hypothesis.\n"
                    "{system_message}\n"
                    "\n=== TECHNICAL CONTEXT PROVIDED ===\n"
                    "{technical_context}\n"
                    "==================================\n\n"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(technical_context=technical_context)
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm
        result = chain.invoke(state["messages"])

        return {
            "messages": [result],
            "market_report": result.content,
        }

    return market_analyst_node
