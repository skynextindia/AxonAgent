# axonai/agents/analysts/market_analyst.py

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from axonai.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)

def create_market_analyst(llm):
    def market_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        ticker = state["company_of_interest"]
        instrument_context = build_instrument_context(ticker, asset_type)

        world_state = state.get("world_state", {})
        market_evidence = state.get("market_evidence", {})
        trader_hypothesis = state.get("trader_hypothesis", {})

        system_message = f"""You are a Market Technical Analyst. Your task is to evaluate the Trader's proposed hypothesis using the structured WorldState and MarketEvidence.

## Proposed Trader Hypothesis:
- **Direction**: {trader_hypothesis.get('direction')}
- **Entry**: {trader_hypothesis.get('entry')}
- **Stop Loss**: {trader_hypothesis.get('sl')}
- **Take Profit**: {trader_hypothesis.get('tp')}
- **Hypothesis**: {trader_hypothesis.get('hypothesis')}

## Pre-flight WorldState:
- **Dominant Regime**: {world_state.get('dominant_regime')} (Confidence: {world_state.get('regime_confidence')})
- **Regime Scores**: {world_state.get('regime_scores')}
- **Volatility Regime**: {world_state.get('volatility_regime')} (ATR H1: {world_state.get('atr_14_h1')} pips)
- **Session**: {world_state.get('session')} (Quality: {world_state.get('session_quality')})

## Technical MarketEvidence:
- **Trend H1**: {market_evidence.get('trend_direction_h1')}
- **Trend H4**: {market_evidence.get('trend_direction_h4')}
- **RSI H1**: {market_evidence.get('rsi_h1')}
- **MACD H1**: {market_evidence.get('macd_signal_h1')}
- **Swing Highs**: {market_evidence.get('swing_highs')}
- **Swing Lows**: {market_evidence.get('swing_lows')}
- **Key S/R Levels**: {market_evidence.get('key_levels')}
- **Recent Patterns**: {market_evidence.get('recent_patterns')}
- **Asian Session Range**: {market_evidence.get('asian_range_low')} to {market_evidence.get('asian_range_high')}
- **London Bias**: {market_evidence.get('london_open_bias')}

## Your Focus:
Does the evidence support the specific trader hypothesis? Perform a sharp, rigorous validation/invalidation. 
Detail exactly:
1. Supporting technical facts (with prices/levels).
2. Opposing technical facts (potential invalidation risks).
3. Final technical verdict (Support / Reject) with confidence score 0-1.
Make sure to include a Markdown table at the end of the report summarizing key technical signals, their direction, and supporting evidence.
{get_language_instruction()}"""

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants. "
                    "Analyze the provided technical context and evaluate the hypothesis.\n"
                    "{system_message}\n"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm
        result = chain.invoke(state["messages"])

        return {
            "messages": [result],
            "market_report": result.content,
        }

    return market_analyst_node
