"""Core simulation of a "faded level broke -> cut" exit rule.

THE RULE (counterfactual, never armed): a fade is entered expecting a level to
HOLD (a SELL fades resistance above; a BUY fades support below). If price pushes
adversely *through* that level by a buffer, the fade thesis is invalidated -- so
exit right there at ``-(level_dist + buffer)`` pips instead of riding to the full
hard stop.

DATA + ITS ONE HARD LIMIT
-------------------------
Per trade we have MFE (max favorable) and MAE (max adverse) *magnitudes*, plus the
faded level's distance/side. We do NOT have the price PATH, so we cannot know the
ORDER of events -- whether a trade poked adverse (breaking the level) BEFORE or
AFTER it made its profit. Because a real-time cut rule must act on the FIRST break
(it cannot see the future), the honest model is: if MAE reached the break
threshold, the cut fires and realizes ``-(level_dist+buffer)`` REGARDLESS of how
the trade actually ended. That correctly:
  * rescues the full-stop losers (adverse ran to the wall)  -> big savings, and
  * penalizes winners that briefly broke then reverted      -> the real cost.
This "naive first-break cut" is the *lower bound* on benefit. We also report an
*optimistic* bound (cut only the trades that actually ended as losses) so the true
value is bracketed, not overstated.

All pip magnitudes are already computed by the loader. Nothing here mutates state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from research.direction_location_forensics.loader import ForensicTrade, _pip_size


def adverse_side_dist_pips(t: ForensicTrade) -> Optional[float]:
    """Signed check: is the faded S/R level on the ADVERSE side of the entry, and
    if so how far (in pips)? Returns the distance when the level is where the fade
    thesis says it should be (resistance above a SELL / support below a BUY), else
    ``None`` (rule does not apply -- e.g. the sell-into-support geometry)."""
    if t.direction not in ("BUY", "SELL"):
        return None
    if t.entry_price is None or t.sr_level_price is None:
        return None
    pip = _pip_size(t.symbol or "")
    delta = (t.sr_level_price - t.entry_price) / pip  # +ve => level above entry
    if t.direction == "SELL":
        return delta if delta >= 0 else None           # resistance above
    else:  # BUY
        return -delta if delta <= 0 else None           # support below


@dataclass
class CutResult:
    ticket: Optional[int]
    direction: Optional[str]
    symbol: Optional[str]
    actual_pips: float
    fired: bool
    cut_pips: Optional[float]      # counterfactual realized pips if the rule fired
    delta: float                   # cut_pips - actual_pips (naive model); 0 if not fired
    kind: str                      # "saver" | "casualty" | "neutral" | "n/a"
    level_dist: Optional[float]
    mae: Optional[float]
    break_thresh: Optional[float]


def simulate_trade(t: ForensicTrade, buffer_pips: float) -> CutResult:
    """Apply the naive first-break cut to one trade at a given buffer."""
    base = CutResult(t.ticket, t.direction, t.symbol,
                     float(t.pips) if t.pips is not None else 0.0,
                     False, None, 0.0, "n/a", None, t.mae_pips, None)
    if t.pips is None or t.mae_pips is None:
        return base
    dist = adverse_side_dist_pips(t)
    if dist is None:
        return base                       # rule doesn't apply to this geometry
    base.level_dist = round(dist, 2)
    thresh = dist + buffer_pips
    base.break_thresh = round(thresh, 2)
    # If the hard stop is TIGHTER than the break threshold, price would hit the
    # stop before the cut -> the rule changes nothing.
    if t.stop_pips is not None and thresh > t.stop_pips + 1e-9:
        return base
    if t.mae_pips + 1e-9 < thresh:
        return base                       # adverse never reached the break -> no fire
    # Fire: exit at the break, a loss of (dist + buffer) pips.
    cut = -(dist + buffer_pips)
    base.fired = True
    base.cut_pips = round(cut, 2)
    base.delta = round(cut - base.actual_pips, 2)
    if base.delta > 1e-9:
        base.kind = "saver"
    elif base.delta < -1e-9:
        base.kind = "casualty"
    else:
        base.kind = "neutral"
    return base


def simulate_all(trades: List[ForensicTrade], buffer_pips: float) -> List[CutResult]:
    return [simulate_trade(t, buffer_pips) for t in trades]


def summarize(results: List[CutResult]) -> Dict[str, Any]:
    fired = [r for r in results if r.fired]
    savers = [r for r in fired if r.kind == "saver"]
    casualties = [r for r in fired if r.kind == "casualty"]
    net = round(sum(r.delta for r in fired), 1)
    saved = round(sum(r.delta for r in savers), 1)
    cost = round(sum(r.delta for r in casualties), 1)
    # Optimistic bound: only cut trades that ACTUALLY ended as losses (best case,
    # assumes a perfect break-detector that never clips a winner).
    opt = [r for r in fired if r.actual_pips < 0]
    opt_net = round(sum(r.delta for r in opt if r.delta > 0), 1)
    return {
        "n_fired": len(fired),
        "n_savers": len(savers),
        "n_casualties": len(casualties),
        "pips_saved": saved,
        "pips_cost": cost,
        "net_delta_naive": net,          # realistic lower bound
        "net_delta_optimistic": opt_net,  # perfect-detector upper bound
    }


def buffer_sweep(trades: List[ForensicTrade],
                 buffers: List[float]) -> List[Dict[str, Any]]:
    out = []
    for b in buffers:
        s = summarize(simulate_all(trades, b))
        s["buffer"] = b
        out.append(s)
    return out


def tail_share(all_lead: List[ForensicTrade]) -> Dict[str, Any]:
    """FULL-history (no MFE needed): how much of total lead loss is full-stop
    SELL losers -- the tail the cut rule targets. Uses exit_reason only."""
    def is_full_stop(t):
        return bool(t.exit_reason and "Stop Loss" in t.exit_reason)
    total_loss = sum(t.pips for t in all_lead if t.pips and t.pips < 0)
    fs_sell = [t for t in all_lead if t.outcome == "LOSS" and is_full_stop(t)
               and t.direction == "SELL"]
    fs_sell_loss = sum(t.pips for t in fs_sell if t.pips)
    fs_all = [t for t in all_lead if t.outcome == "LOSS" and is_full_stop(t)]
    fs_all_loss = sum(t.pips for t in fs_all if t.pips)
    return {
        "total_loss_pips": round(total_loss, 1),
        "full_stop_all_loss_pips": round(fs_all_loss, 1),
        "full_stop_all_n": len(fs_all),
        "full_stop_sell_loss_pips": round(fs_sell_loss, 1),
        "full_stop_sell_n": len(fs_sell),
        "full_stop_sell_share": (round(fs_sell_loss / total_loss, 3)
                                 if total_loss else None),
    }
