from axonai.agents.utils.agent_utils import get_language_instruction


AGENT_NAME = "MARKS"
AGENT_IDENTITY = "AxonAI neutral risk analyst. Evaluates risk-adjusted return objectively. Finds the optimal balance between SIMONS and DALIO positions based on current market conditions."

def create_neutral_debator(llm):
    def neutral_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        neutral_history = risk_debate_state.get("neutral_history", "")

        current_aggressive_response = risk_debate_state.get("current_aggressive_response", "")
        current_conservative_response = risk_debate_state.get("current_conservative_response", "")

        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        asset_type = state.get("asset_type", "stock")
        fundamentals_label = "Macroeconomic Fundamentals Report" if asset_type == "forex" else "Company Fundamentals Report"
        trader_decision = state["trader_investment_plan"]

        munger_verdict = state.get("investment_plan", {})
        munger_conf = munger_verdict.get("confidence", 0) if isinstance(munger_verdict, dict) else 0

        prompt = f"""You are MARKS — AxonAI neutral risk analyst. Find the optimal risk-adjusted position between SIMONS and DALIO.

TRADER PLAN (TUDOR): {trader_decision}

MUNGER CONFIDENCE: {munger_conf}

SIMONS (aggressive): {current_aggressive_response or 'not yet provided'}

DALIO (conservative): {current_conservative_response or 'not yet provided'}

Synthesis rules:
1. Both approve → approve at average lot multiplier
2. SIMONS approves + DALIO reduces → reduce at DALIO's multiplier
3. Either rejects → reject unless MUNGER confidence>85
4. Never approve what DALIO rejects unless MUNGER confidence>85

Output ONLY this JSON, nothing else:
{{"recommendation": "approve|reduce|reject", "final_lot_multiplier": 0.25-1.5, "risk_score": 0-100, "simons_weight": 0.0-1.0, "dalio_weight": 0.0-1.0, "reason": "one sentence"}}""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Neutral Analyst: {response.content}"

        new_risk_debate_state = {
            "history": history + "\n" + argument,
            "aggressive_history": risk_debate_state.get("aggressive_history", ""),
            "conservative_history": risk_debate_state.get("conservative_history", ""),
            "neutral_history": neutral_history + "\n" + argument,
            "latest_speaker": "Neutral",
            "current_aggressive_response": risk_debate_state.get(
                "current_aggressive_response", ""
            ),
            "current_conservative_response": risk_debate_state.get("current_conservative_response", ""),
            "current_neutral_response": argument,
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return neutral_node
