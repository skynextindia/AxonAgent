"""Portfolio Manager: synthesises the risk-analyst debate into the final decision.

Uses LangChain's ``with_structured_output`` so the LLM produces a typed
``PortfolioDecision`` directly, in a single call.  The result is rendered
back to markdown for storage in ``final_trade_decision`` so memory log,
CLI display, and saved reports continue to consume the same shape they do
today.  When a provider does not expose structured output, the agent falls
back gracefully to free-text generation.
"""

from __future__ import annotations

from axonai.agents.schemas import DruckenmillerDecision
from axonai.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)
from axonai.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


AGENT_NAME = "DRUCKENMILLER"
AGENT_IDENTITY = "AxonAI portfolio manager and final execution authority. Makes the definitive execute or reject decision. Has absolute veto power. Nothing trades without DRUCKENMILLER approval."

def create_portfolio_manager(llm):
    structured_llm = llm.with_structured_output(DruckenmillerDecision)

    def portfolio_manager_node(state) -> dict:
        instrument_context = build_instrument_context(state["company_of_interest"])

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        research_plan = state["investment_plan"]
        trader_plan = state["trader_investment_plan"]

        past_context = state.get("past_context", "")
        lessons_line = (
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        prompt = """You are DRUCKENMILLER — AxonAI portfolio manager and absolute final execution authority. Nothing trades without your approval.

You receive all previous agent outputs. You make the final execute or reject decision.

Hard rejection rules — these cannot be overridden by any other agent:
- Asian session active → REJECT
- Spread above 2.0 pips → REJECT  
- MUNGER confidence below 60 → REJECT
- MARKS recommendation is reject → REJECT
- CRITICAL news event within 30 minutes → REJECT
- Account equity drawdown exceeds 5% today → REJECT

Conditional approval rules:
- All hard rules pass AND MARKS approves → APPROVE
- All hard rules pass AND MARKS reduces → APPROVE with reduced lot size
- MUNGER confidence 60-70 → reduce lot size by 50% before approving

When you approve: you are authorizing real money to move. Be certain.
When you reject: state the exact rule that triggered rejection.

Respond with this exact JSON — no other text:
{
  "execute": true|false,
  "direction": "BUY|SELL|HOLD",
  "final_lot_size": 0.00,
  "confidence": 0-100,
  "reason": "one sentence explaining the decision",
  "abort_reason": "null if executing, exact rejection rule if rejecting"
}"""

        prompt += f"\n{lessons_line}"

        try:
            final_trade_decision = structured_llm.invoke(prompt)
            final_trade_decision = final_trade_decision.dict() if hasattr(final_trade_decision, "dict") else final_trade_decision
        except Exception as e:
            final_trade_decision = {
                "execute": False,
                "direction": "HOLD",
                "final_lot_size": 0.0,
                "confidence": 0,
                "reason": "Structured output failed",
                "abort_reason": "system_error"
            }

        new_risk_debate_state = {
            "judge_decision": final_trade_decision,
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
        }

    return portfolio_manager_node
