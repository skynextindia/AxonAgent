# axonai/agents/analysts/news_analyst.py

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from axonai.agents.utils.agent_utils import (
    build_instrument_context,
    get_global_news,
    get_language_instruction,
    get_news,
)

AGENT_NAME = "REUTERS"
AGENT_IDENTITY = "AxonAI news and event analyst. Specialist in identifying high-impact market-moving events and classifying their directional impact on EURUSD in real time."

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

        system_message = """You are REUTERS — AxonAI news and event analyst. Specialist in identifying high-impact market-moving events and classifying their directional impact on EURUSD.

Apply this news impact hierarchy strictly:
CRITICAL: FOMC decision, ECB rate decision, US CPI, US NFP
HIGH: ECB press conference, Fed speeches, EU CPI, German GDP, US GDP
MEDIUM: PMI releases, retail sales, consumer confidence
LOW: Minor central bank speeches, regional data

Rules:
- Only report events that are MEDIUM impact or higher
- For each event state: event name, actual vs expected, EUR impact (positive/negative/neutral)
- If no high-impact events found state "No high-impact events in current window"
- Maximum 150 words total

Respond with this exact JSON at the end of your response:
{"impact": "high|medium|low|none", "events": ["event1", "event2"], "bias": "bullish|bearish|neutral", "confidence": 0-100, "summary": "max 150 words"}"""

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants. "
                    "Use the provided tools to search news and evaluate the hypothesis.\n"
                    "CRITICAL INSTRUCTION: The news tools return static data for the given date range. They do not support custom keyword queries. DO NOT call the tools more than once. Once you receive the news data, generate your final analysis immediately.\n"
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
