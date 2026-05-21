"""Research Manager: turns the bull/bear debate into a structured investment plan for the trader."""

from __future__ import annotations

from axonai.agents.schemas import ResearchPlan, render_research_plan
from axonai.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)
from axonai.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


def create_research_manager(llm):
    structured_llm = bind_structured(llm, ResearchPlan, "Research Manager")

    def research_manager_node(state) -> dict:
        instrument_context = build_instrument_context(state["company_of_interest"])
        investment_debate_state = state["investment_debate_state"]
        bull_history = investment_debate_state.get("bull_history", "")
        bear_history = investment_debate_state.get("bear_history", "")

        history = f"### Bull Analyst Case:\n{bull_history}\n\n### Bear Analyst Case:\n{bear_history}"

        prompt = f"""As the Research Manager and debate facilitator, your role is to critically evaluate the arguments made by the Bull and Bear analysts, score their arguments, and deliver a clear, structured investment plan.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction in the bull thesis; recommend taking or growing the position
- **Overweight**: Constructive view; recommend gradually increasing exposure
- **Hold**: Balanced view; recommend maintaining the current position
- **Underweight**: Cautious view; recommend trimming exposure
- **Sell**: Strong conviction in the bear thesis; recommend exiting or avoiding the position

Commit to a clear stance whenever the debate's strongest arguments warrant one; reserve Hold for situations where the evidence on both sides is genuinely balanced.

---

**Bull and Bear Case Content:**
{history}

You MUST explicitly evaluate, score, and populate the following in your structured response:
1. **supporting_arguments**: Identify the 3 strongest supporting arguments for the trader's hypothesis, each scored with a confidence between 0-1 (e.g. '- H1 EMA alignment indicates strong momentum (Confidence: 0.85)').
2. **opposing_arguments**: Identify the 3 strongest opposing arguments against the trader's hypothesis, each scored with a confidence between 0-1.
3. **missing_assumptions**: List key missing assumptions or market blind spots.
4. **overall_confidence**: Set a final overall confidence score (0-1) reflecting your overall conviction in the final recommendation.
5. **recommendation**, **rationale**, and **strategic_actions**: Set your rating recommendation, natural rationale, and tactical sizing/position guidance for the trader.
""" + get_language_instruction()

        investment_plan = invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_research_plan,
            "Research Manager",
            schema=ResearchPlan,
        )

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

