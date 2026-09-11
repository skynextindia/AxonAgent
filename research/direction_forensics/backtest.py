"""Run the shadow challenger over history and score it. READ-ONLY.

R = realized pips / stop distance (risk-normalized). Baseline keeps every trade;
the challenger keeps only non-vetoed trades. We report, exactly per the brief:
rejected wrong-direction, rejected clean winners (false vetoes), net R retained,
losses avoided, trade-count change, max drawdown, profit factor, and slices by
symbol / direction / regime / date.

A veto can only REMOVE a trade — it never adds or flips one — so the challenger
can never manufacture a winner. This is the honest ceiling of a direction FILTER.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .dataset import Sample
from .challenger import ChallengerPolicy, decide
from research.direction_location_forensics.loader import parse_ts


def _R(s: Sample) -> Optional[float]:
    if s.pips is None:
        return None
    if s.stop_pips and s.stop_pips > 0:
        return s.pips / s.stop_pips
    return s.pips / 20.0  # fallback: nominal 20-pip risk unit (documented)


def _pf(rs: List[float]) -> Optional[float]:
    g = sum(r for r in rs if r > 0)
    l = -sum(r for r in rs if r < 0)
    if l <= 0:
        return None if g <= 0 else float("inf")
    return round(g / l, 3)


def _max_dd(rs_chrono: List[float]) -> float:
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for r in rs_chrono:
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return round(dd, 3)


@dataclass
class BacktestResult:
    policy: str = ""
    n_total: int = 0
    n_kept: int = 0
    n_vetoed: int = 0
    trade_count_change: int = 0
    # rejection accounting
    vetoed_wrong_direction: int = 0
    vetoed_clean_winners: int = 0        # false vetoes
    vetoed_other_loss: int = 0
    vetoed_no_label: int = 0
    # R accounting
    baseline_net_R: float = 0.0
    kept_net_R: float = 0.0
    net_R_retained_pct: Optional[float] = None
    losses_avoided_R: float = 0.0        # sum of -R over vetoed losers (positive = good)
    winners_forgone_R: float = 0.0       # sum of R over vetoed winners (positive = bad)
    baseline_PF: Optional[float] = None
    kept_PF: Optional[float] = None
    baseline_maxDD_R: float = 0.0
    kept_maxDD_R: float = 0.0
    baseline_winrate: Optional[float] = None
    kept_winrate: Optional[float] = None
    slices: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _winrate(samps: List[Sample]) -> Optional[float]:
    dec = [s for s in samps if s.outcome in ("WIN", "LOSS")]
    if not dec:
        return None
    return round(100.0 * sum(s.outcome == "WIN" for s in dec) / len(dec), 1)


def _slice(kept: List[Sample], vetoed: List[Sample], keyfn) -> Dict[str, Any]:
    import collections
    out = {}
    alls = collections.defaultdict(lambda: {"kept": [], "vetoed": []})
    for s in kept:
        alls[keyfn(s)]["kept"].append(s)
    for s in vetoed:
        alls[keyfn(s)]["vetoed"].append(s)
    for k, d in alls.items():
        kr = [r for r in (_R(s) for s in d["kept"]) if r is not None]
        out[str(k)] = {
            "n_kept": len(d["kept"]), "n_vetoed": len(d["vetoed"]),
            "kept_net_R": round(sum(kr), 2),
            "vetoed_wrong_dir": sum(s.fine_bucket == "wrong_direction" for s in d["vetoed"]),
            "vetoed_winners": sum(s.fine_bucket == "clean_win" for s in d["vetoed"]),
        }
    return out


def run_backtest(samples: List[Sample], policy: ChallengerPolicy) -> BacktestResult:
    ordered = sorted(samples, key=lambda s: (parse_ts(s) or __import__("datetime").datetime.min))
    kept: List[Sample] = []
    vetoed: List[Sample] = []
    for s in ordered:
        action, _reasons, _votes = decide(s, policy)
        (vetoed if action == "HOLD" else kept).append(s)

    res = BacktestResult(policy=policy.describe(), n_total=len(ordered),
                         n_kept=len(kept), n_vetoed=len(vetoed),
                         trade_count_change=len(kept) - len(ordered))

    base_R = [r for r in (_R(s) for s in ordered) if r is not None]
    kept_R = [r for r in (_R(s) for s in kept) if r is not None]
    res.baseline_net_R = round(sum(base_R), 3)
    res.kept_net_R = round(sum(kept_R), 3)
    if res.baseline_net_R != 0:
        res.net_R_retained_pct = round(100.0 * res.kept_net_R / res.baseline_net_R, 1)
    res.baseline_PF = _pf(base_R)
    res.kept_PF = _pf(kept_R)
    res.baseline_maxDD_R = _max_dd(base_R)
    res.kept_maxDD_R = _max_dd(kept_R)
    res.baseline_winrate = _winrate(ordered)
    res.kept_winrate = _winrate(kept)

    for s in vetoed:
        r = _R(s)
        if s.fine_bucket == "wrong_direction":
            res.vetoed_wrong_direction += 1
        elif s.fine_bucket == "clean_win":
            res.vetoed_clean_winners += 1
        elif s.outcome == "LOSS":
            res.vetoed_other_loss += 1
        else:
            res.vetoed_no_label += 1
        if r is not None:
            if r < 0:
                res.losses_avoided_R += -r
            elif r > 0:
                res.winners_forgone_R += r
    res.losses_avoided_R = round(res.losses_avoided_R, 3)
    res.winners_forgone_R = round(res.winners_forgone_R, 3)

    res.slices = {
        "by_symbol": _slice(kept, vetoed, lambda s: s.symbol or "?"),
        "by_direction": _slice(kept, vetoed, lambda s: s.direction or "?"),
        "by_month": _slice(kept, vetoed, lambda s: s.month or "?"),
        "by_regime": _slice(kept, vetoed, lambda s: s.dominant_regime or "unknown"),
    }
    return res
