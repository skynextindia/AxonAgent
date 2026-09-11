"""Classify each reconstructed trade along five dimensions and assign a waterfall
bucket. MEASUREMENT ONLY — no thresholds are tuned to optimise anything; they are
fixed, documented cut-points chosen to describe the data, and are exposed so a
reader can re-slice.

Dimensions
----------
A. Direction quality   — did the faded side have real edge? (MFE vs stop)
B. Location quality    — good side but poor entry spot? (MAE depth, S/R side)
C. Entry-timing quality— faded into momentum / against MTF? (displacement, mtf)
D. Exit quality        — winner-grade excursion given back? (realized / MFE)
E. Risk-state effects   — exit forced by RiskGuard / EOD / veto, not the setup?

Confidence
----------
Grades computed from MFE/MAE are ``high`` confidence. Where MFE/MAE are
UNAVAILABLE (the majority of history), grades fall back to realized-only proxies
and are marked ``proxy`` — the reports keep the two populations separate so a
proxy verdict is never presented as measured fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .loader import ForensicTrade

# ── measurement cut-points (fixed, documented; NOT optimised) ────────────────
DIR_WORKED_MFE_FRAC = 0.50   # MFE >= 50% of stop  => direction had real edge
DIR_DEAD_MFE_FRAC = 0.15     # MFE < 15% of stop   => never worked (wrong side)
LOC_BAD_MAE_FRAC = 0.60      # MAE >= 60% of stop  => deep adverse = poor entry spot
LOC_NEAR_LEVEL_PIPS = 2.0    # faded within 2p of an S/R level (thin room)
TIMING_IMPULSE_HI = 0.55     # displacement_ratio >= 0.55 => faded into momentum
EXIT_WINNER_MFE_FRAC = 1.00  # MFE >= 100% of stop => a TP-worthy winner existed
EXIT_CAPTURE_BAD = 0.35      # realized/MFE < 0.35 on a winner-grade trade => gave it back
FULL_STOP_FRAC = 0.85        # realized <= -85% of stop => took (near) a full stop

# exit reasons that are EXOGENOUS to the trade's own SL/TP setup
_FORCED_REASON_KEYS = {
    "risk limit breach": "riskguard_breach",
    "eod flat": "eod_flat",
    "eod hard flat": "eod_flat",
    "eod profit close": "eod_flat",
    "retest veto": "retest_veto",
    "manual": "manual",
}


def _frac_of_stop(pips: Optional[float], stop_pips: Optional[float]) -> Optional[float]:
    if pips is None or not stop_pips or stop_pips <= 0:
        return None
    return pips / stop_pips


def forced_exit_kind(reason: Optional[str]) -> Optional[str]:
    if not reason:
        return None
    low = reason.lower()
    for key, kind in _FORCED_REASON_KEYS.items():
        if key in low:
            return kind
    return None


@dataclass
class TradeVerdict:
    ticket: Optional[int] = None
    account: str = ""
    symbol: Optional[str] = None
    direction: Optional[str] = None
    outcome: Optional[str] = None
    pips: Optional[float] = None
    # per-dimension grades
    direction_grade: str = "unknown"      # right | weak | wrong | unknown
    location_grade: str = "unknown"       # ok | bad | unknown
    timing_grade: str = "unknown"         # ok | bad | unknown
    exit_grade: str = "unknown"           # ok | gave_back | unknown
    risk_state_effect: Optional[str] = None   # riskguard_breach | eod_flat | retest_veto | manual | None
    # derived measures
    mfe_frac: Optional[float] = None
    mae_frac: Optional[float] = None
    realized_frac: Optional[float] = None
    mfe_capture: Optional[float] = None
    # waterfall
    primary_bucket: str = "unclassified"
    confidence: str = "proxy"             # high (has MFE/MAE) | proxy
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def classify(t: ForensicTrade) -> TradeVerdict:
    v = TradeVerdict(ticket=t.ticket, account=t.account, symbol=t.symbol,
                     direction=t.direction, outcome=t.outcome, pips=t.pips)

    has_exc = (t.mfe_pips is not None and t.mae_pips is not None)
    v.confidence = "high" if has_exc else "proxy"

    v.mfe_frac = _frac_of_stop(t.mfe_pips, t.stop_pips)
    v.mae_frac = _frac_of_stop(abs(t.mae_pips) if t.mae_pips is not None else None, t.stop_pips)
    v.realized_frac = _frac_of_stop(t.pips, t.stop_pips)
    if t.mfe_pips and t.mfe_pips > 0 and t.pips is not None:
        v.mfe_capture = t.pips / t.mfe_pips

    v.risk_state_effect = forced_exit_kind(t.exit_reason)

    # ── A. Direction ────────────────────────────────────────────────────────
    if v.mfe_frac is not None:
        if v.mfe_frac >= DIR_WORKED_MFE_FRAC:
            v.direction_grade = "right"
        elif v.mfe_frac < DIR_DEAD_MFE_FRAC:
            v.direction_grade = "wrong"
        else:
            v.direction_grade = "weak"
    else:
        # proxy from realized only
        if t.outcome == "WIN":
            v.direction_grade = "right"
            v.notes.append("direction inferred from WIN (no MFE)")
        elif v.realized_frac is not None and v.realized_frac <= -FULL_STOP_FRAC:
            v.direction_grade = "unknown"   # full stop: cannot split direction vs location w/o MFE
            v.notes.append("full-stop loss, no MFE -> direction/location indistinguishable")
        else:
            v.direction_grade = "unknown"

    # ── B. Location ───────────────────────────────────────────────────────────
    if v.mae_frac is not None and v.direction_grade in ("right", "weak"):
        v.location_grade = "bad" if v.mae_frac >= LOC_BAD_MAE_FRAC else "ok"
    elif v.mae_frac is not None:
        v.location_grade = "bad" if v.mae_frac >= LOC_BAD_MAE_FRAC else "ok"
    else:
        v.location_grade = "unknown"
    # S/R-side context (adds a note; the fade sitting right on a level = thin room)
    if t.sr_level_dist_pips is not None and abs(t.sr_level_dist_pips) <= LOC_NEAR_LEVEL_PIPS:
        v.notes.append(f"entered within {abs(t.sr_level_dist_pips):.1f}p of {t.sr_level_type or 'a level'} (thin room)")
        if v.location_grade == "unknown":
            v.location_grade = "bad"

    # ── C. Timing ────────────────────────────────────────────────────────────
    if t.displacement_ratio is not None:
        v.timing_grade = "bad" if t.displacement_ratio >= TIMING_IMPULSE_HI else "ok"
        if v.timing_grade == "bad":
            v.notes.append(f"faded into momentum (displacement {t.displacement_ratio:.2f})")
    else:
        v.timing_grade = "unknown"
    # MTF continuation against the fade
    if t.mtf_alignment and t.direction:
        mt = t.mtf_alignment.upper()
        if (t.direction == "SELL" and mt == "BULLISH") or (t.direction == "BUY" and mt == "BEARISH"):
            v.notes.append(f"faded against MTF bias ({mt})")
            if v.timing_grade == "unknown":
                v.timing_grade = "bad"

    # ── D. Exit ───────────────────────────────────────────────────────────────
    if v.mfe_frac is not None and v.mfe_frac >= EXIT_WINNER_MFE_FRAC:
        if v.mfe_capture is not None and v.mfe_capture < EXIT_CAPTURE_BAD:
            v.exit_grade = "gave_back"
            v.notes.append(f"winner-grade MFE {t.mfe_pips:.1f}p, captured only "
                           f"{(v.mfe_capture*100):.0f}%")
        else:
            v.exit_grade = "ok"
    elif v.mfe_frac is not None:
        v.exit_grade = "ok"
    else:
        v.exit_grade = "unknown"

    # ── Waterfall: attribute the PRIMARY story (documented precedence) ────────
    v.primary_bucket = _waterfall(t, v)
    return v


def _waterfall(t: ForensicTrade, v: TradeVerdict) -> str:
    """Assign each trade to the FIRST failing stage.

    Precedence: direction -> location -> timing -> (winner-given-back split
    between risk-state-forced vs bad-exit) -> other forced -> clean.
    Winners with an OK exit are 'clean_win'. Losses we cannot split (no MFE)
    are 'loss_no_excursion_data' so they are never miscounted as a measured cause.
    """
    winner_grade = v.mfe_frac is not None and v.mfe_frac >= EXIT_WINNER_MFE_FRAC
    lost = (t.outcome == "LOSS") or (t.pips is not None and t.pips < 0)

    # clean win: positive result, not a given-back winner
    if not lost and v.exit_grade != "gave_back":
        return "clean_win"

    # Stage 1 — direction: the fade never worked at all.
    if v.direction_grade == "wrong":
        return "wrong_direction"

    # A winner-grade excursion (MFE >= stop) that still ended red proves the
    # direction AND location were good enough to reach a full winner — so a deep
    # MAE on the way does NOT make it 'bad location'. The only remaining question
    # is the EXIT: was the winner given back, or was it force-closed? This check
    # therefore precedes location/timing.
    if winner_grade and lost:
        return "risk_state_distortion" if v.risk_state_effect else "bad_exit"

    # Stage 2 — location: right side, but a poor entry spot (deep adverse) and it
    # never reached winner grade.
    if v.location_grade == "bad" and v.direction_grade in ("right", "weak"):
        return "bad_location"
    # Stage 3 — timing: faded into momentum / against MTF.
    if v.timing_grade == "bad" and v.direction_grade in ("right", "weak", "unknown") \
            and v.location_grade in ("ok", "unknown"):
        return "bad_timing"
    # Stage 4 — exit: any other given-back excursion.
    if v.exit_grade == "gave_back":
        return "bad_exit"

    # Stage 5 — a fine-looking setup closed by an exogenous cut.
    if v.risk_state_effect and v.direction_grade in ("right", "weak", "unknown"):
        return "risk_state_distortion"

    # loss we genuinely cannot attribute without excursion data
    if lost and v.confidence == "proxy":
        return "loss_no_excursion_data"
    return "unclassified_loss"


def classify_all(trades: List[ForensicTrade]) -> List[TradeVerdict]:
    return [classify(t) for t in trades]
