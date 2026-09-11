"""Orchestrator for the direction forensics. READ-ONLY.

    python -m research.direction_forensics.run_direction_forensics

Builds the labeled dataset, measures feature separation, tests the reversal-vs-
continuation hypothesis, runs the shadow challenger + walk-forward, derives the
A/B/C decision FROM the results, and writes the five reports + datasets.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from .dataset import (build_samples, clean_winners, wrong_direction,
                      ALWAYS_FEATURES, PARTIAL_FEATURES, ALWAYS_CATEGORICAL,
                      PARTIAL_CATEGORICAL, HYPOTHESIS_MAP)
from . import stats
from .challenger import ChallengerPolicy
from .backtest import run_backtest
from .walkforward import run_walk_forward, _grid, _train_objective
from . import report as R


def _best_full_history_policy(samples) -> ChallengerPolicy:
    """The grid policy with the best FULL-sample objective — used only to SHOW the
    ceiling of a filter fit with hindsight (explicitly NOT the validated choice)."""
    best, best_obj = None, float("-inf")
    for p in _grid():
        obj = _train_objective(samples, p)
        if obj > best_obj:
            best, best_obj = p, obj
    return best or ChallengerPolicy(name="noop")


def _hypothesis_block(cw, wd) -> Dict[str, Any]:
    comp_to_feat = {
        "extreme_velocity": "velocity_divergence",
        "deteriorating_efficiency": "tick_efficiency",
        "strong_displacement": "displacement_ratio",
        "persistent_efficiency": "tick_efficiency",
    }
    tests = []
    powers = []
    for comp, feat in comp_to_feat.items():
        auc = stats.rank_auc(cw, wd, feat)
        if auc is None:
            tests.append([f"{comp}→{feat}", "n/a (n<3)", "untestable (too few labeled)"])
            continue
        power = abs(auc - 0.5)
        powers.append(power)
        verdict = ("separates" if power >= 0.10 else "no separation (≈chance)")
        tests.append([f"{comp}→{feat}", round(auc, 3), verdict])
    max_power = max(powers) if powers else 0.0
    if max_power >= 0.10:
        concl = ("At least one hypothesis component shows weak separation; see the "
                 "walk-forward before trusting it.")
    else:
        concl = ("None of the testable hypothesis components separate winning fades "
                 "from run-over fades (all AUC ≈ 0.5). The proposed reversal-vs-"
                 "continuation signature is NOT present in the existing telemetry.")
    return {"map": HYPOTHESIS_MAP, "tests": tests, "conclusion": concl,
            "max_power": max_power}


def _decision(feat_fine, hyp, wf, n_cw, n_wd, months_fine) -> Dict[str, Any]:
    best_power = max((r["power"] or 0.0) for r in feat_fine) if feat_fine else 0.0
    any_ci_excludes = any(r["ci_excludes_0.5"] for r in feat_fine)
    generalizes = wf.generalizes
    august_only = wf.august_only
    fine_one_month = len(months_fine) <= 1

    if generalizes and any_ci_excludes and best_power >= 0.10:
        label = "A — VALIDATED DIRECTION FILTER CANDIDATE"
        statement = ("A decision-point feature separates winning fades from run-over "
                     "fades with a bootstrap CI excluding chance, AND a TRAIN-fit veto "
                     "improves both held-out slices. Candidate for a future OFF/shadow "
                     "arming trial — still not shipped here.")
    elif (best_power >= 0.10 or any_ci_excludes or hyp["max_power"] >= 0.10) and not august_only:
        label = "B — PROMISING BUT INSUFFICIENT EVIDENCE"
        statement = ("There is a faint separation in one or two features, but it is "
                     "too weak and/or too thinly sampled to trust, and it does not "
                     "robustly generalize on held-out data. Not a validated filter.")
    else:
        label = "C — NO STABLE DIRECTION FILTER FOUND"
        statement = ("The existing decision-point features do not contain a stable, "
                     "out-of-sample distinction between reversal and continuation. "
                     "Every candidate discriminator sits at chance on the fair labeled "
                     "subset, and no TRAIN-fit veto generalizes to held-out data.")

    evidence = [
        f"Fine-label feature separation (clean_win n={n_cw} vs wrong_direction "
        f"n={n_wd}): best discriminating power |AUC−0.5| = {best_power:.3f}; "
        f"any feature CI excluding 0.5: {any_ci_excludes}.",
        f"Reversal-vs-continuation hypothesis: max component power = "
        f"{hyp['max_power']:.3f} — {hyp['conclusion']}",
        f"Walk-forward: {wf.verdict} (generalizes={generalizes}, "
        f"august_only={august_only}, untrainable={wf.untrainable}).",
        "Feature coverage by split: the discriminating features (displacement, "
        "S/R distance) and MFE labels have ZERO coverage in TRAIN and VALIDATION "
        f"(train displacement={wf.coverage['train']['displacement_ratio']}, "
        f"val displacement={wf.coverage['validation']['displacement_ratio']}); they "
        "exist ONLY in the August OOS window — so a filter using them cannot be "
        "trained or validated out-of-regime on current telemetry.",
        f"Fine-labeled data spans months {sorted(months_fine)} — "
        + ("single month (the adverse regime): no out-of-sample period exists for the "
           "precise labels, so any fine-label threshold is inherently August-fitted."
           if fine_one_month else "multiple months."),
    ]
    why_not = []
    if label[0] != "A":
        why_not.append("Not A: no feature clears both a CI-excludes-chance separation "
                       "AND held-out generalization.")
    if label[0] != "C":
        why_not.append("Not C: a faint, non-generalizing separation exists in at least "
                       "one feature, so the evidence is 'insufficient' rather than 'none'.")
    if label[0] == "C":
        why_not.append("Not B: the separation is at chance on the fair subset and the "
                       "temporal core of the hypothesis (velocity decay/acceleration) is "
                       "absent from telemetry, so there is nothing 'promising' to carry forward.")
    to_upgrade = [
        "Log the VELOCITY TRAJECTORY (a short pre-signal velocity/efficiency series), "
        "not a single snapshot — the hypothesis's decay/acceleration terms need it.",
        "Log MFE/MAE on EVERY trade (currently recent-only) so the fine labels span "
        "more than the August regime and a real train/validation/OOS split becomes possible.",
        "Accumulate a second independent adverse regime before fitting any threshold — "
        "the current fine labels are 100% one month.",
    ]
    return {"label": label, "statement": statement, "evidence": evidence,
            "why_not": why_not, "to_upgrade": to_upgrade}


def main() -> int:
    samples = build_samples()
    cw = clean_winners(samples, mfe_only=True)
    wd = wrong_direction(samples)

    all_feats = ALWAYS_FEATURES + PARTIAL_FEATURES
    feat_fine = stats.feature_ranking(cw, wd, all_feats)

    wins = [s for s in samples if s.outcome == "WIN"]
    losses = [s for s in samples if s.outcome == "LOSS"]
    feat_coarse = stats.feature_ranking(wins, losses, all_feats)

    categoricals = {c: stats.categorical_split(cw, wd, c)
                    for c in ALWAYS_CATEGORICAL + PARTIAL_CATEGORICAL}

    hyp = _hypothesis_block(cw, wd)
    best_policy = _best_full_history_policy(samples)
    bt = run_backtest(samples, best_policy)
    wf = run_walk_forward(samples)

    months_fine = {s.month for s in (cw + wd) if s.month}
    decision = _decision(feat_fine, hyp, wf, len(cw), len(wd), months_fine)

    paths = R.write_datasets(samples, feat_fine, feat_coarse)
    ctx = {
        "feat_fine": feat_fine, "feat_coarse": feat_coarse,
        "n_clean_win_mfe": len(cw), "n_wrong_dir": len(wd),
        "categoricals": categoricals, "hypothesis": hyp,
        "backtest_best": bt, "walk_forward": wf, "decision": decision,
    }
    md = R.write_all(ctx)

    sp = os.path.join(R.OUT_DIR, "direction_summary.json")
    R._assert_isolated(sp)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump({"decision": decision, "feat_fine": feat_fine,
                   "hypothesis_max_power": hyp["max_power"],
                   "walk_forward_verdict": wf.verdict,
                   "backtest": bt.to_dict()}, f, indent=1)

    print("=== DIRECTION FORENSICS ===")
    print(f"samples={len(samples)}  clean_win(MFE)={len(cw)}  wrong_direction={len(wd)}")
    print(f"fine-label months: {sorted(months_fine)}")
    print("\ntop features (fine, clean_win vs wrong_dir):")
    for r in feat_fine[:6]:
        print(f"  {r['feature']:22s} AUC={r['auc']} power={r['power']} CI={r['ci']} excl0.5={r['ci_excludes_0.5']}")
    print(f"\nhypothesis max power: {hyp['max_power']:.3f} — {hyp['conclusion'][:80]}...")
    print(f"walk-forward: {wf.verdict}")
    print(f"\n>>> DECISION: {decision['label']}")
    print("reports:", ", ".join(os.path.basename(p) for p in md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
