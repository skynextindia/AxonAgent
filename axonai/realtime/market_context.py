"""MarketContext: Single immutable source of truth for market state per tick.

Aggregates all math engine outputs (velocity, displacement, liquidity, location, mtf, regime)
into a frozen dataclass. This ensures every decision module sees the same version of reality.

Includes quality scores for detecting:
- Reversal lag (how many ticks delayed?)
- Stop hunting (are stops being manipulated?)
- Signal ambiguity (how clear is the reversal?)
- Consensus strength (what % of engines agree?)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from axonai.realtime.velocity_normalizer import NormalizedVelocity
from axonai.realtime.displacement_engine import DisplacementState
from axonai.realtime.liquidity_engine import LiquidityState
from axonai.realtime.location_engine import LocationContext
from axonai.realtime.mtf_context import MTFState
from axonai.realtime.regime_engine import RegimeState


@dataclass(frozen=True)
class MarketContext:
    """Complete market state for one tick + confidence/quality scores.

    This is the single immutable source of truth for all decision modules.
    Every component (EntryStateMachine, TradeStateEngine, ExitEngine) reads from this.
    """

    # ========== RAW MARKET DATA ==========
    timestamp: datetime
    price: float
    bid: float
    ask: float
    volume: int

    # ========== ENGINE OUTPUTS (6 math engines) ==========

    # 1. Velocity Engine
    velocity: NormalizedVelocity

    # 2. Displacement Engine
    displacement: DisplacementState

    # 3. Liquidity Engine
    liquidity: LiquidityState

    # 4. Location Engine (support/resistance)
    location: LocationContext

    # 5. Multi-Timeframe Context
    mtf: MTFState

    # 6. Regime Engine
    regime: RegimeState

    # ========== QUALITY SCORES (context layers) ==========

    # Layer 3: Reversal Lag Detection
    reversal_lag_ticks: int = 0
    """How many ticks since signal triggered vs confirmation? (0 = immediate)"""

    is_lagged: bool = False
    """Is reversal slow/extended?"""

    lag_severity: str = "NONE"
    """'NONE' / 'LIGHT' (1-3 ticks) / 'HEAVY' (4+ ticks)"""

    # Layer 4: Stop Hunting Detection
    stop_hunt_detected: bool = False
    """Is price manipulating stops?"""

    stop_hunt_severity: float = 0.0
    """0-100, how aggressive is the stop hunt?"""

    stop_hunt_phase: str = "NORMAL"
    """'NORMAL' / 'HUNTING' / 'SWEEPING' / 'REVERSING'"""

    # Layer 5: Reversal Ambiguity / Confirmation Strength
    reversal_confidence: float = 0.0
    """0-100, how clear is this reversal?

    Scoring:
    - 80-100: STRONG reversal (multiple engines agree, multiple confirmations)
    - 60-79: MODERATE reversal (2-3 engines agree, some ambiguity)
    - 40-59: WEAK reversal (signals mixed, ambiguous)
    - 0-39: VERY WEAK (likely noise/false signal)
    """

    signal_agreement_score: float = 0.0
    """0-100, what % of engines agree? (engines: velocity, displacement, location, regime, mtf, liquidity)

    Example:
    - If all 6 engines agree: 100%
    - If 4 of 6 agree: 67%
    - If 3 of 6 agree: 50%
    - If 2 of 6 agree: 33%
    - If 1 of 6 agree: 17%
    """

    displacement_phase: str = "EARLY"
    """'EARLY' (just triggered) / 'CONFIRMING' (strengthening) / 'CONFIRMED' (multiple candles)

    Use to adjust entry timing:
    - EARLY: Wait 2-3 ticks before entering (signal still forming)
    - CONFIRMING: Wait 1 tick (getting clearer)
    - CONFIRMED: Enter immediately (signal established)
    """

    signals_that_agree: List[str] = field(default_factory=list)
    """List of engines signaling reversal: ['VELOCITY_SPIKE', 'DISPLACEMENT_IMPULSE', 'REGIME_SHIFT', ...]"""

    signals_that_disagree: List[str] = field(default_factory=list)
    """List of engines NOT signaling reversal: ['LOCATION_AT_RESISTANCE', 'MTF_OPPOSING']"""

    consensus_verdict: str = "UNCERTAIN"
    """Overall decision:
    - 'STRONG_REVERSAL': 80%+ confidence, multiple confirmations, no ambiguity
    - 'MODERATE_REVERSAL': 60-79% confidence, some signals mixed
    - 'WEAK_REVERSAL': 40-59% confidence, ambiguous
    - 'AMBIGUOUS': 0-39% confidence, too much noise
    - 'RANGE_CHOP': No clear reversal direction
    """

    # Layer 6: Entry Quality & Timing
    best_entry_already_missed: bool = False
    """Did price move away before confirmation? (lag issue)"""

    optimal_entry_price: float = 0.0
    """Where we *should* have entered for best risk/reward"""

    current_entry_slippage_pips: float = 0.0
    """Current price - optimal entry (positive = worse than ideal)"""

    entry_window_closing: bool = False
    """Is the reversal opportunity closing? (time-sensitive)"""

    ticks_until_confirmation_expires: int = 999
    """How many ticks until this signal becomes invalid? (signals decay)"""

    # ========== DIAGNOSTIC INFO ==========

    engines_with_high_confidence: List[str] = field(default_factory=list)
    """Which engines are >70% confident in their classification?"""

    summary: str = ""
    """Human-readable summary of market state for logging/debugging"""


def build_market_context_summary(ctx: MarketContext) -> str:
    """Generate human-readable summary of market context."""
    return (
        f"MarketContext | Price: {ctx.price:.5f} | "
        f"Regime: {ctx.regime.regime} | "
        f"Velocity: {ctx.velocity.percentile:.0f}th | "
        f"Displacement: {ctx.displacement.classification} | "
        f"Confidence: {ctx.reversal_confidence:.0f}% | "
        f"Agreement: {ctx.signal_agreement_score:.0f}% | "
        f"Verdict: {ctx.consensus_verdict}"
    )
