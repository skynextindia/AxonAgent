from axonai.agents.utils.agent_utils import get_language_instruction


AGENT_NAME = "SIMONS"
AGENT_IDENTITY = "AxonAI aggressive risk analyst. Advocates for maximum position sizing when mathematical edge is confirmed. Argues for full execution when signal quality is high."

def create_aggressive_debator(llm):
    def aggressive_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        aggressive_history = risk_debate_state.get("aggressive_history", "")

        current_conservative_response = risk_debate_state.get("current_conservative_response", "")
        current_neutral_response = risk_debate_state.get("current_neutral_response", "")

        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        asset_type = state.get("asset_type", "stock")
        fundamentals_label = "Macroeconomic Fundamentals Report" if asset_type == "forex" else "Company Fundamentals Report"
        trader_decision = state["trader_investment_plan"]

        prompt = """You are SIMONS — AxonAI aggressive risk analyst. You advocate for maximum position sizing when mathematical edge is confirmed.

You receive TUDOR's execution parameters and MUNGER's verdict.
Your job: argue for full execution at the proposed lot size when signal quality justifies it.

Approve full execution when:
- MUNGER confidence is above 70
- RR ratio is above 1.5
- Session is London or New York or Overlap
- Spread is below 1.5 pips
- No CRITICAL news events in the next 30 minutes

Argue for size reduction (not rejection) when:
- Confidence is 60-70
- RR ratio is 1.3-1.5
- Session is approaching rollover

Always reject when:
- MUNGER confidence below 60
- Spread above 2.5 pips
- Asian session

Respond with this exact JSON:
{"recommendation": "approve|reduce|reject", "suggested_lot_multiplier": 0.5-1.5, "risk_score": 0-100, "reason": "one sentence"}""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Aggressive Analyst: {response.content}"

        new_risk_debate_state = {
            "history": history + "\n" + argument,
            "aggressive_history": aggressive_history + "\n" + argument,
            "conservative_history": risk_debate_state.get("conservative_history", ""),
            "neutral_history": risk_debate_state.get("neutral_history", ""),
            "latest_speaker": "Aggressive",
            "current_aggressive_response": argument,
            "current_conservative_response": risk_debate_state.get("current_conservative_response", ""),
            "current_neutral_response": risk_debate_state.get(
                "current_neutral_response", ""
            ),
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return aggressive_node
