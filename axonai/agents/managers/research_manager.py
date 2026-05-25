"""Research Manager: turns the bull/bear debate into a structured investment plan for the trader."""

from __future__ import annotations

from axonai.agents.schemas import MungerVerdict
from axonai.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)
from axonai.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


AGENT_NAME = "MUNGER"
AGENT_IDENTITY = "AxonAI research synthesis manager. Weighs bull and bear arguments, scores conviction on both sides, identifies unresolved conflicts, and produces the final directional verdict."

def create_research_manager(llm):
    structured_llm = llm.with_structured_output(MungerVerdict)

    def research_manager_node(state) -> dict:
        instrument_context = build_instrument_context(state["company_of_interest"])
        investment_debate_state = state["investment_debate_state"]
        bull_history = investment_debate_state.get("bull_history", "")
        bear_history = investment_debate_state.get("bear_history", "")

        history = f"### Bull Analyst Case:\n{bull_history}\n\n### Bear Analyst Case:\n{bear_history}"

        prompt = """You are MUNGER — AxonAI research synthesis manager. You receive BUFFETT's bull case and SOROS's bear case and produce the definitive directional verdict.

Your process must be explicit:
1. Score BUFFETT's arguments: evaluate each argument 0-100 for strength and evidence quality
2. Score SOROS's arguments: evaluate each counter-argument 0-100 for strength
3. Identify the single most important unresolved conflict between them
4. Identify the single most important assumption that if wrong invalidates the trade
5. Produce a final verdict with overall confidence

Scoring rules:
- Arguments backed by specific data score higher than general claims
- Arguments that reference provided evidence score higher than general knowledge
- If confidence is below 55 the verdict must be HOLD regardless of direction

Respond with this exact JSON structure — no other text:
{
  "direction": "BUY|SELL|HOLD",
  "confidence": 0-100,
  "bull_score": 0-100,
  "bear_score": 0-100,
  "key_conflict": "single sentence describing main unresolved conflict",
  "missing_assumption": "single sentence describing critical unresolved assumption",
  "supporting_arguments": ["top bull arg", "second bull arg", "third bull arg"],
  "opposing_arguments": ["top bear arg", "second bear arg", "third bear arg"],
  "overall_confidence": 0-100
}""" + get_language_instruction()

        try:
            investment_plan = structured_llm.invoke(prompt)
            investment_plan_dict = investment_plan.dict() if hasattr(investment_plan, "dict") else investment_plan
        except Exception as e:
            investment_plan_dict = {
                "direction": "HOLD",
                "confidence": 0,
                "bull_score": 0,
                "bear_score": 0,
                "key_conflict": "Error: structured output failed",
                "missing_assumption": str(e),
                "supporting_arguments": [],
                "opposing_arguments": [],
                "overall_confidence": 0
            }
        investment_plan = investment_plan_dict

        new_investment_debate_state = {
            "judge_decision": investment_plan,
            "history": history,
            "bear_history": bear_history,
            "bull_history": bull_history,
            "current_response": investment_plan,
            "count": investment_debate_state.get("count", 1),
        }

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": investment_plan,
        }

    return research_manager_node

