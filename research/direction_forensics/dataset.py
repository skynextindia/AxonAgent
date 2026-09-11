"""Reconstruct the labeled feature dataset for the direction question.

Reuses the sibling READ-ONLY loader/classifier so labels stay consistent with the
prior forensic. For each lead trade it flattens the decision-point features and
attaches the outcome labels:

    CURRENT_SIGNAL_DIRECTION  — the faded BUY/SELL the daemon actually produced
    ACTUAL_PRICE_OUTCOME      — WIN / LOSS (realized) + MFE/MAE where present
    REVERSAL_SUCCESS          — the fade worked (WIN, or MFE-worked)
    CONTINUATION_FAILURE      — the fade was run over (wrong_direction bucket)

and the fine bucket from the waterfall (clean_win / wrong_direction / bad_location
/ bad_timing / bad_exit / risk_state_distortion / loss_no_excursion_data).

Nothing here is written back to any journal.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from research.direction_location_forensics.loader import (
    ForensicTrade, load_all, parse_ts)
from research.direction_location_forensics.classify import classify, TradeVerdict

# Decision-point features, grouped by availability so the reports can be honest
# about which are present for every trade vs only a recent subset.
ALWAYS_FEATURES = [
    "velocity_divergence", "tick_efficiency", "peak_confidence", "spread_pips",
]
ALWAYS_CATEGORICAL = ["peak_type", "mtf_alignment", "intensity", "divergence_warning"]
PARTIAL_FEATURES = [
    "displacement_ratio", "sr_level_dist_pips", "regime_confidence",
]
PARTIAL_CATEGORICAL = ["sr_level_type", "structure_state", "dominant_regime", "daily_trend"]

# Features named by the reversal-vs-continuation hypothesis, mapped to what the
# telemetry actually holds. Anything the logs do not contain is marked here so the
# report states plainly what CANNOT be tested.
HYPOTHESIS_MAP = {
    "extreme_velocity": ("velocity_divergence", "present (static snapshot)"),
    "velocity_decay": (None, "UNAVAILABLE — logs a single velocity value, not its trajectory"),
    "deteriorating_efficiency": ("tick_efficiency", "present (static snapshot; low=deteriorating)"),
    "reversal_pressure": ("divergence_warning", "proxy only (boolean warning + velocity_divergence)"),
    "exhaustion_flag": ("peak_type", "partial (peak_type contains 'exhaustion'; regime er_exhaustion on 23 rows)"),
    "continued_acceleration": (None, "UNAVAILABLE — no velocity trajectory in logs"),
    "strong_displacement": ("displacement_ratio", "present on 72 rows only"),
    "persistent_efficiency": ("tick_efficiency", "present (static snapshot; high=persistent)"),
    "directional_liquidity_break": ("breakout_fresh_extreme", "proxy only, 64 rows (breakout_shadow)"),
}


@dataclass
class Sample:
    # identity
    ticket: Optional[int] = None
    account: str = "lead"
    symbol: Optional[str] = None
    timestamp: Optional[str] = None
    timestamp_utc: Optional[str] = None
    month: Optional[str] = None
    # decision-point features (UNAVAILABLE stays None)
    direction: Optional[str] = None              # CURRENT_SIGNAL_DIRECTION
    velocity_divergence: Optional[float] = None
    tick_efficiency: Optional[float] = None
    peak_confidence: Optional[float] = None
    spread_pips: Optional[float] = None
    peak_type: Optional[str] = None
    mtf_alignment: Optional[str] = None
    intensity: Optional[str] = None
    divergence_warning: Optional[bool] = None
    displacement_ratio: Optional[float] = None
    sr_level_dist_pips: Optional[float] = None
    sr_level_type: Optional[str] = None
    structure_state: Optional[str] = None
    dominant_regime: Optional[str] = None
    daily_trend: Optional[str] = None
    regime_confidence: Optional[float] = None
    # outcome / labels
    pips: Optional[float] = None
    stop_pips: Optional[float] = None
    mfe_pips: Optional[float] = None
    mae_pips: Optional[float] = None
    outcome: Optional[str] = None                # ACTUAL_PRICE_OUTCOME (WIN/LOSS)
    fine_bucket: str = "unclassified"
    confidence: str = "proxy"
    reversal_success: Optional[bool] = None      # the fade worked
    continuation_failure: Optional[bool] = None  # wrong_direction (run over)
    has_mfe: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _reversal_success(t: ForensicTrade, v: TradeVerdict) -> Optional[bool]:
    # WIN => the faded reversal played out. LOSS with dead MFE => not a reversal.
    if t.outcome == "WIN":
        return True
    if v.direction_grade == "right":       # MFE showed real favorable excursion
        return True
    if v.direction_grade == "wrong":
        return False
    return None                             # cannot tell without excursion data


def build_samples() -> List[Sample]:
    lead = load_all()["lead"]
    out: List[Sample] = []
    for t in lead:
        v = classify(t)
        dt = parse_ts(t)
        cf = (v.primary_bucket == "wrong_direction")
        out.append(Sample(
            ticket=t.ticket, symbol=t.symbol, timestamp=t.timestamp,
            timestamp_utc=t.timestamp_utc,
            month=dt.strftime("%Y-%m") if dt else None,
            direction=t.direction,
            velocity_divergence=t.velocity_divergence,
            tick_efficiency=t.tick_efficiency,
            peak_confidence=t.peak_confidence,
            spread_pips=t.spread_pips,
            peak_type=t.peak_type,
            mtf_alignment=t.mtf_alignment,
            intensity=None,  # intensity lives in event_details.intensity; join below if needed
            divergence_warning=None,
            displacement_ratio=t.displacement_ratio,
            sr_level_dist_pips=t.sr_level_dist_pips,
            sr_level_type=t.sr_level_type,
            structure_state=t.structure_state,
            dominant_regime=t.dominant_regime,
            daily_trend=t.daily_trend,
            regime_confidence=t.regime_confidence,
            pips=t.pips, stop_pips=t.stop_pips,
            mfe_pips=t.mfe_pips, mae_pips=t.mae_pips,
            outcome=t.outcome,
            fine_bucket=v.primary_bucket,
            confidence=v.confidence,
            reversal_success=_reversal_success(t, v),
            continuation_failure=cf,
            has_mfe=(t.mfe_pips is not None and t.mae_pips is not None),
        ))
    return out


# convenience group selectors used across the analysis
def clean_winners(samples: List[Sample], mfe_only: bool = True) -> List[Sample]:
    return [s for s in samples if s.fine_bucket == "clean_win"
            and (s.has_mfe or not mfe_only)]


def wrong_direction(samples: List[Sample]) -> List[Sample]:
    return [s for s in samples if s.fine_bucket == "wrong_direction"]
