from axonai.agents.utils.agent_utils import get_language_instruction


AGENT_NAME = "DALIO"
AGENT_IDENTITY = "AxonAI conservative risk analyst. Prioritizes capital preservation above all else. Argues for position size reduction or rejection when any significant risk factor is present."

def create_conservative_debator(llm):
    def conservative_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        conservative_history = risk_debate_state.get("conservative_history", "")

        current_aggressive_response = risk_debate_state.get("current_aggressive_response", "")
        current_neutral_response = risk_debate_state.get("current_neutral_response", "")

        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        asset_type = state.get("asset_type", "stock")
        fundamentals_label = "Macroeconomic Fundamentals Report" if asset_type == "forex" else "Company Fundamentals Report"
        trader_decision = state["trader_investment_plan"]

        munger_verdict = state.get("investment_plan", {})
        munger_conf = munger_verdict.get("confidence", 0) if isinstance(munger_verdict, dict) else 0
        munger_dir = munger_verdict.get("direction", "?") if isinstance(munger_verdict, dict) else "?"

        prompt = f"""You are DALIO — AxonAI conservative risk analyst. Capital preservation is your primary mandate.

TRADER PLAN (TUDOR): {trader_decision}

MUNGER VERDICT: {munger_dir} confidence={munger_conf}

PEER VIEWS:
- SIMONS (aggressive): {current_aggressive_response or 'not yet provided'}
- MARKS (neutral): {current_neutral_response or 'not yet provided'}

Reduce size when ANY present: upcoming high-impact news<60min, spread>1.5 pips, confidence<70, H4 conflicts direction, 3+ consecutive losses
Always reject: Asian session, spread>2.5 pips, CRITICAL news<30min, drawdown>3% this session

Output ONLY this JSON, nothing else:
{{"recommendation": "approve|reduce|reject", "suggested_lot_multiplier": 0.25-1.0, "risk_score": 0-100, "reason": "one sentence", "primary_concern": "one sentence"}}""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Conservative Analyst: {response.content}"

        new_risk_debate_state = {
            "history": history + "\n" + argument,
            "aggressive_history": risk_debate_state.get("aggressive_history", ""),
            "conservative_history": conservative_history + "\n" + argument,
            "neutral_history": risk_debate_state.get("neutral_history", ""),
            "latest_speaker": "Conservative",
            "current_aggressive_response": risk_debate_state.get(
                "current_aggressive_response", ""
            ),
            "current_conservative_response": argument,
            "current_neutral_response": risk_debate_state.get(
                "current_neutral_response", ""
            ),
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return conservative_node
