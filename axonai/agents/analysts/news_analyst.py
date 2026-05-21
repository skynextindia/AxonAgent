# axonai/agents/analysts/news_analyst.py

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from axonai.agents.utils.agent_utils import (
    build_instrument_context,
    get_global_news,
    get_language_instruction,
    get_news,
)

def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
        ticker = state["company_of_interest"]
        instrument_context = build_instrument_context(ticker, asset_type)

        world_state = state.get("world_state", {})
        market_evidence = state.get("market_evidence", {})
        trader_hypothesis = state.get("trader_hypothesis", {})

        tools = [
            get_news,
            get_global_news,
        ]

        system_message = f"""You are a News Macro Analyst. Your task is to evaluate the Trader's proposed hypothesis using the structured WorldState, MarketEvidence, and news content.

## Proposed Trader Hypothesis:
- **Direction**: {trader_hypothesis.get('direction')}
- **Entry**: {trader_hypothesis.get('entry')}
- **Stop Loss**: {trader_hypothesis.get('sl')}
- **Take Profit**: {trader_hypothesis.get('tp')}
- **Hypothesis**: {trader_hypothesis.get('hypothesis')}

## Pre-flight WorldState:
- **Dominant Regime**: {world_state.get('dominant_regime')} (Confidence: {world_state.get('regime_confidence')})
- **Session**: {world_state.get('session')}

## Technical MarketEvidence:
- **Trend H1**: {market_evidence.get('trend_direction_h1')}
- **Key S/R Levels**: {market_evidence.get('key_levels')}

## Your Focus:
Does current macroeconomic news or {asset_label}-specific news support the specific trader hypothesis?
You MUST call the available tools: get_news(query, start_date, end_date) fortargeted news searches, and get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news to gather actual headlines.
Then, perform a sharp, rigorous validation/invalidation:
1. Supporting macroeconomic or news facts.
2. Opposing news facts (potential macroeconomic invalidation risks).
3. Final news verdict (Support / Reject) with confidence score 0-1.
Make sure to include a Markdown table at the end of the report summarizing key news signals, their direction, and supporting evidence.
{get_language_instruction()}"""

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants. "
                    "Use the provided tools to search news and evaluate the hypothesis.\n"
                    "{system_message}\n"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        report = ""
        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node
