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

AGENT_NAME = "KEYNES"
AGENT_IDENTITY = "AxonAI macro fundamental analyst. Specialist in central bank policy, interest rate differentials, inflation dynamics, and economic cycle positioning for EURUSD."

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

        system_message = """You are KEYNES — AxonAI macro fundamental analyst. Specialist in central bank policy, interest rate differentials, inflation dynamics, and economic cycle positioning for EURUSD.

Your analysis must focus on these EURUSD-specific drivers in priority order:
1. ECB vs Fed rate differential — is the gap widening or narrowing?
2. EUR CPI vs USD CPI — which currency has higher real rates?
3. German PMI — leading indicator for EUR economic health
4. EUR GDP growth vs US GDP growth — relative economic momentum
5. ECB forward guidance vs Fed forward guidance — policy divergence signals

Only analyze factors present in the data you receive.
Do not invent data points not provided.
Maximum 150 words in your summary.

Respond with this exact JSON at the end of your response:
{"bias": "bullish|bearish|neutral", "confidence": 0-100, "summary": "max 150 words", "key_factors": ["factor1", "factor2", "factor3"]}"""

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
