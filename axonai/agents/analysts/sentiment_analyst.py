# axonai/agents/analysts/sentiment_analyst.py

from datetime import datetime, timedelta
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from axonai.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
    get_news,
)
from axonai.dataflows.reddit import fetch_reddit_posts
from axonai.dataflows.stocktwits import fetch_stocktwits_messages

def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")

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
        stocktwits_block = fetch_stocktwits_messages(ticker, limit=30)
        reddit_block = fetch_reddit_posts(ticker)

        system_message = f"""You are a Sentiment Analyst. Your task is to evaluate the Trader's proposed hypothesis using the pre-fetched news headlines, StockTwits, and Reddit posts.

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

## Data sources (pre-fetched):
### News headlines — Yahoo Finance, past 7 days
<start_of_news>
{news_block}
<end_of_news>

### StockTwits messages
<start_of_stocktwits>
{stocktwits_block}
<end_of_stocktwits>

### Reddit posts — past 7 days
<start_of_reddit>
{reddit_block}
<end_of_reddit>

## Your Focus:
Does the crowd and news sentiment support the specific trader hypothesis? Perform a sharp, rigorous validation/invalidation:
1. Supporting sentiment facts (cite message ratios or notable comments).
2. Opposing sentiment facts (potential crowd invalidation risks).
3. Final sentiment verdict (Support / Reject) with confidence score 0-1.
Make sure to include a Markdown table at the end of the report summarizing key sentiment signals, their direction, and supporting evidence.
{get_language_instruction()}"""

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
