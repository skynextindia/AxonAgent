# DIRECTION_DECISION.md

> READ-ONLY / SHADOW. No production change is proposed or made. rank-AUC 0.5 = no separation; distance from 0.5 = discriminating power. 'fine' labels (clean_win vs wrong_direction) require MFE/MAE and exist only on a recent, August-concentrated subset; 'coarse' labels (WIN vs LOSS) span the full history.


# FINAL DECISION: C — NO STABLE DIRECTION FILTER FOUND


The existing decision-point features do not contain a stable, out-of-sample distinction between reversal and continuation. Every candidate discriminator sits at chance on the fair labeled subset, and no TRAIN-fit veto generalizes to held-out data.


## Evidence summary

- Fine-label feature separation (clean_win n=32 vs wrong_direction n=29): best discriminating power |AUC−0.5| = 0.091; any feature CI excluding 0.5: False.

- Reversal-vs-continuation hypothesis: max component power = 0.091 — None of the testable hypothesis components separate winning fades from run-over fades (all AUC ≈ 0.5). The proposed reversal-vs-continuation signature is NOT present in the existing telemetry.

- Walk-forward: best TRAIN policy vetoes 0 trades on TRAIN+VALIDATION — it is effectively inaction (the discriminating features are absent before August). No filter to validate; its only vetoes fall in OOS/August, i.e. in-sample to the adverse regime. (generalizes=False, august_only=False, untrainable=True).

- Feature coverage by split: the discriminating features (displacement, S/R distance) and MFE labels have ZERO coverage in TRAIN and VALIDATION (train displacement=0, val displacement=0); they exist ONLY in the August OOS window — so a filter using them cannot be trained or validated out-of-regime on current telemetry.

- Fine-labeled data spans months ['2026-08'] — single month (the adverse regime): no out-of-sample period exists for the precise labels, so any fine-label threshold is inherently August-fitted.


## Why not the other outcomes

- Not A: no feature clears both a CI-excludes-chance separation AND held-out generalization.

- Not B: the separation is at chance on the fair subset and the temporal core of the hypothesis (velocity decay/acceleration) is absent from telemetry, so there is nothing 'promising' to carry forward.


## What would change the verdict (evidence to gather, not build)

- Log the VELOCITY TRAJECTORY (a short pre-signal velocity/efficiency series), not a single snapshot — the hypothesis's decay/acceleration terms need it.

- Log MFE/MAE on EVERY trade (currently recent-only) so the fine labels span more than the August regime and a real train/validation/OOS split becomes possible.

- Accumulate a second independent adverse regime before fitting any threshold — the current fine labels are 100% one month.


_No candidate is implemented into production in this phase._
