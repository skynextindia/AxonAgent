# axonai/agents/analysts/sentiment_analyst.py

from datetime import datetime, timedelta
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from axonai.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
    get_news,
)
from axonai.dataflows.reddit import fetch_reddit_posts
from axonai.dataflows.forex_social import fetch_forex_social_feed

def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")

AGENT_NAME = "LIVERMORE"
AGENT_IDENTITY = "AxonAI market sentiment analyst. Specialist in reading institutional positioning, COT data, DXY correlation, and crowd psychology to identify smart money direction."

def create_sentiment_analyst(llm):
    def sentiment_analyst_node(state):
        ticker = state["company_of_interest"]
        end_date = state["trade_date"]
        start_date = _seven_days_back(end_date)
        instrument_context = build_instrument_context(ticker)

        world_state = state.get("world_state", {})
        market_evidence = state.get("market_evidence", {})
        trader_hypothesis = state.get("trader_hypothesis", {})

        # Pre-fetch three sources
        news_block = get_news.func(ticker, start_date, end_date)
        forex_social_block = fetch_forex_social_feed(ticker, limit=30)
        reddit_block = fetch_reddit_posts(ticker)

        # Broadcast active sentiment feeds to Dashboard cockpit
        try:
            from axonai.realtime.api_server import get_dashboard
            dashboard = get_dashboard()
            if dashboard:
                dashboard.broadcast({
                    "type": "news_data",
                    "news": news_block,
                    "forex_social": forex_social_block,
                    "reddit": reddit_block,
                    "timestamp": datetime.now().strftime("%H:%M:%S")
                })
        except Exception:
            pass

        system_message = """You are LIVERMORE — AxonAI market sentiment analyst. Specialist in reading institutional positioning, COT data, DXY correlation, and crowd psychology.

Your analysis must focus on:
- COT positioning: are large speculators net long or short EUR? Is positioning extreme?
- DXY correlation: is USD strengthening or weakening independently of EUR?
- Risk sentiment: is the market risk-on (EUR positive) or risk-off (EUR negative)?
- Retail vs institutional divergence: are retail traders positioned against smart money?
- Positioning extremes: is the market too long or too short creating reversal risk?

Only analyze data you receive. Do not invent positioning data.
Maximum 150 words in your summary.

Respond with this exact JSON at the end of your response:
{"bias": "bullish|bearish|neutral", "confidence": 0-100, "summary": "max 150 words", "positioning": "crowded_long|crowded_short|balanced|unknown"}"""

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants. "
                    "Analyze the provided sentiment context and evaluate the hypothesis.\n"
                    "{system_message}\n"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=end_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm
        result = chain.invoke(state["messages"])

        return {
            "messages": [result],
            "sentiment_report": result.content,
        }

    return sentiment_analyst_node

def create_social_media_analyst(llm):
    return create_sentiment_analyst(llm)
