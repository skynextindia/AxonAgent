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

        # Check background cache first to avoid slow blocking queries on start/restart
        news_block = None
        forex_social_block = None
        reddit_block = None
        try:
            from axonai.realtime.api_server import get_dashboard
            dashboard = get_dashboard()
            if dashboard and dashboard.history.get("news_data"):
                cache = dashboard.history["news_data"]
                news_block = cache.get("news")
                forex_social_block = cache.get("forex_social")
                reddit_block = cache.get("reddit")
        except Exception:
            pass

        # Fallback to online fetch if cache is empty or offline backfill is required
        if not news_block:
            news_block = get_news.func(ticker, start_date, end_date)
        if not forex_social_block:
            forex_social_block = fetch_forex_social_feed(ticker, limit=30)
        if not reddit_block:
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

        # Construct sentiment context block for LIVERMORE
        sentiment_context = ""
        if world_state:
            sentiment_context += "### WorldState Sentiment Indicators:\n"
            sentiment_indicators = ["regime", "session", "belief_score", "dxy_momentum", "risk_sentiment", "cot_positioning", "retail_sentiment"]
            for k in sentiment_indicators:
                if k in world_state:
                    sentiment_context += f"- {k}: {world_state[k]}\n"

        if news_block:
            sentiment_context += f"\n### News Context:\n{str(news_block)[:2000]}\n"
        if forex_social_block:
            sentiment_context += f"\n### Forex Social Feed:\n{str(forex_social_block)[:1000]}\n"
        if reddit_block:
            sentiment_context += f"\n### Reddit Posts Sentiment:\n{str(reddit_block)[:1000]}\n"

        if not sentiment_context:
            sentiment_context = "No pre-computed sentiment, news, or positioning data is available."

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
                    "\n=== SENTIMENT CONTEXT PROVIDED ===\n"
                    "{sentiment_context}\n"
                    "==================================\n\n"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(sentiment_context=sentiment_context)
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
