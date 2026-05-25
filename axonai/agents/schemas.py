"""Pydantic schemas used by agents that produce structured output.

The framework's primary artifact is still prose: each agent's natural-language
reasoning is what users read in the saved markdown reports and what the
downstream agents read as context.  Structured output is layered onto the
three decision-making agents (Research Manager, Trader, Portfolio Manager)
so that:

- Their outputs follow consistent section headers across runs and providers
- Each provider's native structured-output mode is used (json_schema for
  OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic)
- Schema field descriptions become the model's output instructions, freeing
  the prompt body to focus on context and the rating-scale guidance
- A render helper turns the parsed Pydantic instance back into the same
  markdown shape the rest of the system already consumes, so display,
  memory log, and saved reports keep working unchanged
"""

from __future__ import annotations

from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Validation schemas for analyst, researcher, and risk agents
# ---------------------------------------------------------------------------


class AnalystOutput(BaseModel):
    """Post-hoc validation schema for all 4 analyst agents."""
    bias: Literal["bullish", "bearish", "neutral"] = Field(
        description="Directional bias of the analysis.",
    )
    confidence: int = Field(
        ge=0, le=100,
        description="Confidence in the bias, 0-100.",
    )
    summary: str = Field(
        description="Summary of the analyst's findings, max 150 words.",
    )
    key_factors: List[str] = Field(
        max_length=3,
        description="Top 3 key factors driving the bias.",
    )


class ResearchOutput(BaseModel):
    """Post-hoc validation schema for bull/bear researchers."""
    position: Literal["bull", "bear"] = Field(
        description="Whether this is a bull or bear argument.",
    )
    arguments: List[str] = Field(
        max_length=3,
        description="Top 3 arguments, each max 50 words.",
    )
    confidence: int = Field(
        ge=0, le=100,
        description="Confidence in the position, 0-100.",
    )
    key_risk: str = Field(
        description="Single most important risk to the position.",
    )


class RiskOutput(BaseModel):
    """Post-hoc validation schema for risk debator agents."""
    recommendation: Literal["approve", "reduce", "reject"] = Field(
        description="Risk recommendation for the trade.",
    )
    risk_score: int = Field(
        ge=0, le=100,
        description="Aggregate risk score, 0-100.",
    )
    reason: str = Field(
        description="Primary reason for the recommendation.",
    )


# ---------------------------------------------------------------------------
# Shared rating types
# ---------------------------------------------------------------------------


class PortfolioRating(str, Enum):
    """5-tier rating used by the Research Manager and Portfolio Manager."""

    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class TraderAction(str, Enum):
    """3-tier transaction direction used by the Trader.

    The Trader's job is to translate the Research Manager's investment plan
    into a concrete transaction proposal: should the desk execute a Buy, a
    Sell, or sit on Hold this round.  Position sizing and the nuanced
    Overweight / Underweight calls happen later at the Portfolio Manager.
    """

    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


# ---------------------------------------------------------------------------
# Research Manager
# ---------------------------------------------------------------------------


class ResearchPlan(BaseModel):
    """Structured investment plan produced by the Research Manager.

    Hand-off to the Trader: the recommendation pins the directional view,
    the rationale captures which side of the bull/bear debate carried the
    argument, and the strategic actions translate that into concrete
    instructions the trader can execute against.
    """

    recommendation: PortfolioRating = Field(
        description=(
            "The investment recommendation. Exactly one of Buy / Overweight / "
            "Hold / Underweight / Sell. Reserve Hold for situations where the "
            "evidence on both sides is genuinely balanced; otherwise commit to "
            "the side with the stronger arguments."
        ),
    )
    rationale: str = Field(
        description=(
            "Conversational summary of the key points from both sides of the "
            "debate, ending with which arguments led to the recommendation. "
            "Speak naturally, as if to a teammate."
        ),
    )
    strategic_actions: str = Field(
        description=(
            "Concrete steps for the trader to implement the recommendation, "
            "including position sizing guidance consistent with the rating."
        ),
    )
    supporting_arguments: str = Field(
        description="3 strongest supporting arguments with confidence 0-1 each, formatted as a bullet list.",
    )
    opposing_arguments: str = Field(
        description="3 strongest opposing arguments with confidence 0-1 each, formatted as a bullet list.",
    )
    missing_assumptions: str = Field(
        description="Key missing assumptions or blind spots identified.",
    )
    overall_confidence: float = Field(
        description="Overall confidence score as a float between 0.0 and 1.0.",
    )


def render_research_plan(plan: ResearchPlan) -> str:
    """Render a ResearchPlan to markdown for storage and the trader's prompt context."""
    return "\n".join([
        f"**Recommendation**: {plan.recommendation.value}",
        "",
        f"**Overall Confidence**: {plan.overall_confidence:.2f}",
        "",
        f"**Rationale**: {plan.rationale}",
        "",
        f"**Strategic Actions**: {plan.strategic_actions}",
        "",
        f"**3 Strongest Supporting Arguments**:",
        f"{plan.supporting_arguments}",
        "",
        f"**3 Strongest Opposing Arguments**:",
        f"{plan.opposing_arguments}",
        "",
        f"**Missing Assumptions**:",
        f"{plan.missing_assumptions}",
    ])



# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


class TraderProposal(BaseModel):
    """Structured transaction proposal produced by the Trader.

    The trader reads the Research Manager's investment plan and the analyst
    reports, then turns them into a concrete transaction: what action to
    take, the reasoning that justifies it, and the practical levels for
    entry, stop-loss, take-profit, and sizing.
    """

    action: TraderAction = Field(
        description="The transaction direction. Exactly one of Buy / Hold / Sell.",
    )
    reasoning: str = Field(
        description=(
            "The case for this action, anchored in the analysts' reports and "
            "the research plan. Two to four sentences."
        ),
    )
    entry_price: float = Field(
        description="The precise entry price target in the instrument's quote currency.",
    )
    stop_loss: float = Field(
        description="The precise stop-loss price in the instrument's quote currency.",
    )
    take_profit: float = Field(
        description="The precise take-profit price in the instrument's quote currency.",
    )
    position_sizing: Optional[str] = Field(
        default=None,
        description="Optional sizing guidance, e.g. '5% of portfolio'.",
    )


def render_trader_proposal(proposal: TraderProposal) -> str:
    """Render a TraderProposal to markdown.

    The trailing ``FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`` line is
    preserved for backward compatibility with the analyst stop-signal text
    and any external code that greps for it.
    """
    parts = [
        f"**Action**: {proposal.action.value}",
        "",
        f"**Reasoning**: {proposal.reasoning}",
        "",
        f"**Entry Price**: {proposal.entry_price}",
        "",
        f"**Stop Loss**: {proposal.stop_loss}",
        "",
        f"**Take Profit**: {proposal.take_profit}",
    ]
    if proposal.position_sizing:
        parts.extend(["", f"**Position Sizing**: {proposal.position_sizing}"])
    parts.extend([
        "",
        f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**",
    ])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Portfolio Manager
# ---------------------------------------------------------------------------


class PortfolioDecision(BaseModel):
    """Structured output produced by the Portfolio Manager.

    The model fills every field as part of its primary LLM call; no separate
    extraction pass is required. Field descriptions double as the model's
    output instructions, so the prompt body only needs to convey context and
    the rating-scale guidance.
    """

    rating: PortfolioRating = Field(
        description=(
            "The final position rating. Exactly one of Buy / Overweight / Hold / "
            "Underweight / Sell, picked based on the analysts' debate."
        ),
    )
    executive_summary: str = Field(
        description=(
            "A concise action plan covering entry strategy, position sizing, "
            "key risk levels, and time horizon. Two to four sentences."
        ),
    )
    investment_thesis: str = Field(
        description=(
            "Detailed reasoning anchored in specific evidence from the analysts' "
            "debate. If prior lessons are referenced in the prompt context, "
            "incorporate them; otherwise rely solely on the current analysis."
        ),
    )
    price_target: Optional[float] = Field(
        default=None,
        description="Optional target price in the instrument's quote currency.",
    )
    time_horizon: Optional[str] = Field(
        default=None,
        description="Optional recommended holding period, e.g. '3-6 months'.",
    )


def render_pm_decision(decision: PortfolioDecision) -> str:
    """Render a PortfolioDecision back to the markdown shape the rest of the system expects.

    Memory log, CLI display, and saved report files all read this markdown,
    so the rendered output preserves the exact section headers (``**Rating**``,
    ``**Executive Summary**``, ``**Investment Thesis**``) that downstream
    parsers and the report writers already handle.
    """
    parts = [
        f"**Rating**: {decision.rating.value}",
        "",
        f"**Executive Summary**: {decision.executive_summary}",
        "",
        f"**Investment Thesis**: {decision.investment_thesis}",
    ]
    if decision.price_target is not None:
        parts.extend(["", f"**Price Target**: {decision.price_target}"])
    if decision.time_horizon:
        parts.extend(["", f"**Time Horizon**: {decision.time_horizon}"])
    return "\n".join(parts)
from pydantic import BaseModel, Field
from typing import List, Optional

class MungerVerdict(BaseModel):
    direction: str = Field(description="BUY, SELL, or HOLD")
    confidence: int = Field(description="0-100")
    bull_score: int = Field(description="0-100")
    bear_score: int = Field(description="0-100")
    key_conflict: str = Field(description="single sentence describing main unresolved conflict")
    missing_assumption: str = Field(description="single sentence describing critical unresolved assumption")
    supporting_arguments: List[str] = Field(description="top bull args")
    opposing_arguments: List[str] = Field(description="top bear args")
    overall_confidence: int = Field(description="0-100")

class TudorExecution(BaseModel):
    direction: str = Field(description="BUY, SELL, or HOLD")
    entry: float = Field(description="Entry price")
    sl: float = Field(description="Stop loss price")
    tp: float = Field(description="Take profit price")
    lot_size: float = Field(description="Lot size")
    sl_pips: float = Field(description="SL pips")
    tp_pips: float = Field(description="TP pips")
    rr_ratio: float = Field(description="Risk/Reward ratio")
    hypothesis: str = Field(description="one sentence explaining the trade")

class DruckenmillerDecision(BaseModel):
    execute: bool = Field(description="True to execute, False to reject")
    direction: str = Field(description="BUY, SELL, or HOLD")
    final_lot_size: float = Field(description="Final approved lot size")
    confidence: int = Field(description="0-100")
    reason: str = Field(description="one sentence explaining the decision")
    abort_reason: Optional[str] = Field(description="null if executing, exact rejection rule if rejecting")
