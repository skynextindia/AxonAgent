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

        munger_verdict = state.get("investment_plan", {})
        munger_conf = munger_verdict.get("confidence", 0) if isinstance(munger_verdict, dict) else 0
        munger_dir = munger_verdict.get("direction", "?") if isinstance(munger_verdict, dict) else "?"

        prompt = f"""You are SIMONS — AxonAI aggressive risk analyst.

TRADER PLAN (TUDOR): {trader_decision}

MUNGER VERDICT: {munger_dir} confidence={munger_conf}

PEER VIEWS:
- DALIO (conservative): {current_conservative_response or 'not yet provided'}
- MARKS (neutral): {current_neutral_response or 'not yet provided'}

Approve full execution when: MUNGER confidence>70, RR>1.5, London/NY session, spread<1.5 pips, no CRITICAL news
Reduce (not reject) when: confidence 60-70, RR 1.3-1.5, approaching rollover
Always reject: confidence<60, spread>2.5 pips, Asian session

Output ONLY this JSON, nothing else:
{{"recommendation": "approve|reduce|reject", "suggested_lot_multiplier": 0.5-1.5, "risk_score": 0-100, "reason": "one sentence"}}""" + get_language_instruction()

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
