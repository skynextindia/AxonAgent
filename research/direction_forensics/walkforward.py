"""Chronological walk-forward validation. Fit ONLY on TRAIN; freeze; evaluate on
VALIDATION and OUT-OF-SAMPLE. Reject anything that only helps the single August
adverse regime.

Splits (by calendar, fixed in advance — NOT chosen to flatter a result):
    TRAIN       : 2026-06-15 .. 2026-07-15
    VALIDATION  : 2026-07-16 .. 2026-07-31
    OOS         : 2026-08-01 .. 2026-08-18

The fit is a small, honest grid search over the challenger thresholds, scored on
TRAIN only by a risk-normalized objective (kept net R minus a penalty for vetoing
winners). The single best TRAIN policy is then applied unchanged to VALIDATION and
OOS. A candidate is only interesting if it helps (or at least does not hurt) on BOTH
held-out slices — not August alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .dataset import Sample
from .challenger import ChallengerPolicy
from .backtest import run_backtest, _R
from research.direction_location_forensics.loader import parse_ts

TRAIN_END = datetime(2026, 7, 16)
VAL_END = datetime(2026, 8, 1)


def split(samples: List[Sample]) -> Dict[str, List[Sample]]:
    tr, va, oos, unknown = [], [], [], []
    for s in samples:
        dt = parse_ts(s)
        if dt is None:
            unknown.append(s)
        elif dt < TRAIN_END:
            tr.append(s)
        elif dt < VAL_END:
            va.append(s)
        else:
            oos.append(s)
    return {"train": tr, "validation": va, "oos": oos, "undated": unknown}


def _grid() -> List[ChallengerPolicy]:
    """A small, explicit threshold grid. Deliberately coarse: the goal is to see
    whether ANY setting generalizes, not to fine-tune."""
    grid: List[ChallengerPolicy] = []
    disp_opts = [None, 0.45, 0.55, 0.65]
    eff_opts = [None, 0.02, 0.03]
    vel_opts = [None, 1.0, 1.5]
    mtf_opts = [False, True]
    i = 0
    for d in disp_opts:
        for e in eff_opts:
            for v in vel_opts:
                for m in mtf_opts:
                    if d is None and e is None and v is None and not m:
                        continue  # skip the no-op policy
                    i += 1
                    grid.append(ChallengerPolicy(
                        name=f"g{i}", displacement_min=d, efficiency_min=e,
                        velocity_div_max=v, veto_against_mtf=m, min_votes=1))
    return grid


def _train_objective(samps: List[Sample], policy: ChallengerPolicy) -> float:
    """Kept net R with a penalty per vetoed winner (discourages winner-culling).
    Higher = better on TRAIN."""
    from .challenger import decide
    kept_R = 0.0
    winner_veto_pen = 0.0
    for s in samps:
        action, _r, _v = decide(s, policy)
        r = _R(s)
        if action == "HOLD":
            if s.fine_bucket == "clean_win" and r is not None:
                winner_veto_pen += r          # forgone winner R
        else:
            if r is not None:
                kept_R += r
    return kept_R - winner_veto_pen


_PARTIAL_FEATURES_FOR_COVERAGE = ("displacement_ratio", "sr_level_dist_pips",
                                  "regime_confidence")


def _coverage(samps: List[Sample]) -> Dict[str, Any]:
    def cov(f):
        return sum(getattr(s, f, None) is not None for s in samps)
    return {"n": len(samps),
            "mfe": sum(s.has_mfe for s in samps),
            **{f: cov(f) for f in _PARTIAL_FEATURES_FOR_COVERAGE}}


@dataclass
class WalkForwardResult:
    best_policy: str = ""
    fitted_on: str = "train"
    train: Dict[str, Any] = field(default_factory=dict)
    validation: Dict[str, Any] = field(default_factory=dict)
    oos: Dict[str, Any] = field(default_factory=dict)
    coverage: Dict[str, Any] = field(default_factory=dict)
    baseline: Dict[str, Any] = field(default_factory=dict)
    generalizes: bool = False
    august_only: bool = False
    untrainable: bool = False
    verdict: str = ""


def _slice_metrics(samps: List[Sample], policy: ChallengerPolicy) -> Dict[str, Any]:
    r = run_backtest(samps, policy)
    return {
        "n_total": r.n_total, "n_kept": r.n_kept, "n_vetoed": r.n_vetoed,
        "baseline_net_R": r.baseline_net_R, "kept_net_R": r.kept_net_R,
        "delta_R": round(r.kept_net_R - r.baseline_net_R, 3),
        "baseline_PF": r.baseline_PF, "kept_PF": r.kept_PF,
        "vetoed_wrong_direction": r.vetoed_wrong_direction,
        "vetoed_clean_winners": r.vetoed_clean_winners,
        "baseline_maxDD_R": r.baseline_maxDD_R, "kept_maxDD_R": r.kept_maxDD_R,
    }


def run_walk_forward(samples: List[Sample]) -> WalkForwardResult:
    sp = split(samples)
    train = sp["train"]
    if len(train) < 10:
        return WalkForwardResult(
            best_policy="(none)", verdict="INSUFFICIENT TRAIN DATA "
            f"(train n={len(train)}) — walk-forward not possible")

    # fit on TRAIN only
    best, best_obj = None, float("-inf")
    for p in _grid():
        obj = _train_objective(train, p)
        if obj > best_obj:
            best, best_obj = p, obj

    res = WalkForwardResult(best_policy=best.describe())
    res.train = _slice_metrics(train, best)
    res.validation = _slice_metrics(sp["validation"], best)
    res.oos = _slice_metrics(sp["oos"], best)
    res.coverage = {"train": _coverage(train), "validation": _coverage(sp["validation"]),
                    "oos": _coverage(sp["oos"])}

    # Materiality: a policy that vetoes ~nothing on the fit + first held-out window
    # is not a filter — it is inaction that trivially "does not hurt". Detect it.
    train_val_vetoes = res.train["n_vetoed"] + res.validation["n_vetoed"]
    # Untrainable: the genuinely-discriminating (partial) features — displacement and
    # S/R distance — have ZERO coverage in TRAIN, so nothing using them can be fit
    # there. (regime_confidence is always-present but non-discriminating, so it does
    # not count toward trainability.) They exist only in the OOS/August window,
    # making any such rule inherently in-sample to the adverse regime.
    _DISCRIMINATING_PARTIAL = ("displacement_ratio", "sr_level_dist_pips")
    partial_train_cov = sum(res.coverage["train"][f] for f in _DISCRIMINATING_PARTIAL)
    res.untrainable = (partial_train_cov == 0)

    val_ok = res.validation["delta_R"] >= -1e-9 or res.validation["n_total"] < 8
    oos_ok = res.oos["delta_R"] >= -1e-9

    if train_val_vetoes == 0:
        res.generalizes = False
        res.verdict = ("best TRAIN policy vetoes 0 trades on TRAIN+VALIDATION — it is "
                       "effectively inaction (the discriminating features are absent "
                       "before August). No filter to validate; its only vetoes fall in "
                       "OOS/August, i.e. in-sample to the adverse regime.")
    elif res.oos["delta_R"] > 0 and res.validation["delta_R"] < -1e-9:
        res.generalizes = False
        res.august_only = True
        res.verdict = ("REJECT — improves August (OOS) but hurts validation (fitted to "
                       "the single adverse regime).")
    elif val_ok and oos_ok and (res.oos["delta_R"] > 0 or res.validation["delta_R"] > 0):
        res.generalizes = True
        res.verdict = "materially vetoes AND holds/improves both held-out slices"
    else:
        res.generalizes = False
        res.verdict = "no generalizing benefit on held-out data"
    return res
