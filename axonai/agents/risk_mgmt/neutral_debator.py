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

        prompt = """You are MARKS — AxonAI neutral risk analyst. You find the optimal risk-adjusted position between SIMONS and DALIO.

You receive TUDOR's parameters, MUNGER's verdict, SIMONS's recommendation, and DALIO's recommendation.

Your job: synthesize SIMONS and DALIO positions into a rational risk-adjusted verdict.

Process:
1. If both SIMONS and DALIO approve: approve at average of their lot multipliers
2. If SIMONS approves and DALIO reduces: reduce at DALIO's multiplier
3. If either rejects: reject unless there is a compelling specific reason to override
4. Never approve what DALIO rejects unless MUNGER confidence exceeds 85

Respond with this exact JSON:
{"recommendation": "approve|reduce|reject", "final_lot_multiplier": 0.25-1.5, "risk_score": 0-100, "simons_weight": 0.0-1.0, "dalio_weight": 0.0-1.0, "reason": "one sentence"}""" + get_language_instruction()

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
