# axonai/agents/analysts/fundamentals_analyst.py

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from axonai.agents.utils.agent_utils import (
    build_instrument_context,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_language_instruction,
)

def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        ticker = state["company_of_interest"]
        instrument_context = build_instrument_context(ticker, asset_type)

        world_state = state.get("world_state", {})
        market_evidence = state.get("market_evidence", {})
        trader_hypothesis = state.get("trader_hypothesis", {})

        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
        ]

        if asset_type == "forex":
            role_description = (
                "You are a macroeconomic analyst evaluating the fundamental monetary and economic profiles of the currency pair's countries. "
                "Highlight central bank interest rates, CPI inflation, GDP, interest rate differentials, and central bank monetary policy stances (hawkish vs dovish)."
            )
        else:
            role_description = (
                "You are a corporate fundamental researcher evaluating the corporate financial profile of the company. "
                "Highlight key corporate financial metrics, balance sheet strengths, income statements, and cashflows."
            )

        system_message = f"""{role_description}

## Proposed Trader Hypothesis:
- **Direction**: {trader_hypothesis.get('direction')}
- **Entry**: {trader_hypothesis.get('entry')}
- **Stop Loss**: {trader_hypothesis.get('sl')}
- **Take Profit**: {trader_hypothesis.get('tp')}
- **Hypothesis**: {trader_hypothesis.get('hypothesis')}

## Pre-flight WorldState:
- **Dominant Regime**: {world_state.get('dominant_regime')} (Confidence: {world_state.get('regime_confidence')})
- **EUR strength**: {world_state.get('eur_strength')}
- **USD strength**: {world_state.get('usd_strength')}

## Your Focus:
Does the fundamental macroeconomic or corporate profile support the specific trader hypothesis?
You MUST call the available tools: `get_fundamentals` to retrieve fundamental data, and other financial statement tools as needed to justify your evaluation.
Then, perform a sharp, rigorous validation/invalidation:
1. Supporting fundamental facts.
2. Opposing fundamental facts (potential monetary or financial invalidation risks).
3. Final fundamental verdict (Support / Reject) with confidence score 0-1.
Make sure to include a Markdown table at the end of the report summarizing key fundamental signals, their direction, and supporting evidence.
{get_language_instruction()}"""

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants. "
                    "Use the provided tools to gather fundamental data and evaluate the hypothesis.\n"
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
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
