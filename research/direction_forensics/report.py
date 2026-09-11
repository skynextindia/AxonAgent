"""Emit the five markdown reports + datasets. Writes ONLY inside this package.

Nothing here decides the strategy; it renders the measured results and the A/B/C
decision computed by run_direction_forensics from the data.
"""

from __future__ import annotations

import os
import csv
import json
from typing import Any, Dict, List

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(_THIS_DIR, "out")


def _assert_isolated(path: str) -> None:
    ap = os.path.abspath(path).replace("\\", "/").lower()
    if "/reports/signals" in ap:
        raise RuntimeError(f"refuses to write a production journal path: {path}")
    if "/direction_forensics/" not in ap:
        raise RuntimeError(f"writes only inside its package, got: {path}")


def _md_table(headers: List[str], rows: List[list]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    return "\n".join(out)


def write_datasets(samples, feat_fine, feat_coarse) -> Dict[str, str]:
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = {}
    sj = os.path.join(OUT_DIR, "direction_samples.json")
    _assert_isolated(sj)
    with open(sj, "w", encoding="utf-8") as f:
        json.dump([s.to_dict() for s in samples], f, indent=1)
    paths["samples_json"] = sj

    sc = os.path.join(OUT_DIR, "direction_samples.csv")
    _assert_isolated(sc)
    if samples:
        fields = list(samples[0].to_dict().keys())
        with open(sc, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(fields)
            for s in samples:
                d = s.to_dict()
                w.writerow([d.get(k) for k in fields])
    paths["samples_csv"] = sc

    fr = os.path.join(OUT_DIR, "feature_ranking.json")
    _assert_isolated(fr)
    with open(fr, "w", encoding="utf-8") as f:
        json.dump({"fine_clean_vs_wrong": feat_fine, "coarse_win_vs_loss": feat_coarse}, f, indent=1)
    paths["feature_ranking_json"] = fr
    return paths


def _w(name: str, text: str, written: List[str]):
    p = os.path.join(_THIS_DIR, name)
    _assert_isolated(p)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    written.append(p)


def write_all(ctx: Dict[str, Any]) -> List[str]:
    written: List[str] = []
    fine, coarse = ctx["feat_fine"], ctx["feat_coarse"]
    n_cw, n_wd = ctx["n_clean_win_mfe"], ctx["n_wrong_dir"]
    bt = ctx["backtest_best"]
    wf = ctx["walk_forward"]
    hyp = ctx["hypothesis"]
    dec = ctx["decision"]

    disclaimer = ("> READ-ONLY / SHADOW. No production change is proposed or made. "
                  "rank-AUC 0.5 = no separation; distance from 0.5 = discriminating "
                  "power. 'fine' labels (clean_win vs wrong_direction) require MFE/MAE "
                  "and exist only on a recent, August-concentrated subset; 'coarse' "
                  "labels (WIN vs LOSS) span the full history.\n")

    def _feat_rows(ranking):
        rows = []
        for r in ranking:
            ci = r["ci"]
            rows.append([r["feature"], r["auc"], r["power"],
                         f"[{ci[0]},{ci[1]}]" if ci else "n/a",
                         "yes" if r["ci_excludes_0.5"] else "no",
                         r["pos_summary"].get("n"), r["neg_summary"].get("n")])
        return rows

    # 1. DIRECTION_FEATURE_FORENSIC.md
    t = [f"# DIRECTION_FEATURE_FORENSIC.md\n", disclaimer,
         f"\n## Decision-point feature separation: clean winners vs wrong-direction losses\n",
         f"Fair comparison on the MFE-labeled subset (clean_win n={n_cw}, "
         f"wrong_direction n={n_wd}). Features ranked by discriminating power "
         f"(|AUC−0.5|). CI = deterministic bootstrap 95%.\n",
         _md_table(["feature", "AUC", "power", "95% CI", "CI excl 0.5?", "n_pos", "n_neg"],
                   _feat_rows(fine)),
         "\n\n## Same features on the full history (coarse WIN vs LOSS)\n",
         _md_table(["feature", "AUC", "power", "95% CI", "CI excl 0.5?", "n_pos", "n_neg"],
                   _feat_rows(coarse)),
         "\n\n## Categorical features\n"]
    for name, tab in ctx["categoricals"].items():
        rows = [[k, v["pos"], v["neg"]] for k, v in sorted(tab.items(), key=lambda kv: -(kv[1]["pos"]+kv[1]["neg"]))]
        t.append(f"\n**{name}** (pos=clean_win, neg=wrong_direction):\n")
        t.append(_md_table(["value", "clean_win", "wrong_dir"], rows))
        t.append("\n")
    t.append("\n**Reading:** an AUC whose CI excludes 0.5 is a real (if weak) "
             "separator. If every CI straddles 0.5, the decision-point features do "
             "not distinguish a winning fade from a run-over fade.\n")
    _w("DIRECTION_FEATURE_FORENSIC.md", "\n".join(t), written)

    # 2. CONTINUATION_VS_REVERSAL.md
    t = [f"# CONTINUATION_VS_REVERSAL.md\n", disclaimer,
         "\n## The hypothesis, mapped to what the telemetry actually holds\n",
         "The brief's reversal/continuation signatures require these inputs. Several "
         "are NOT in the logs — stated plainly rather than approximated away.\n",
         _md_table(["hypothesis component", "mapped feature", "availability"],
                   [[k, (v[0] or "—"), v[1]] for k, v in hyp["map"].items()]),
         "\n\n## Test of the discriminating components (fine subset)\n",
         _md_table(["component→feature", "AUC (clean_win vs wrong_dir)", "verdict"],
                   hyp["tests"]),
         "\n\n**Key limitation:** velocity *decay* and *continued acceleration* — the "
         "temporal core of the hypothesis — cannot be measured: the journal logs a "
         "single static `velocity_divergence` per trade, not its trajectory. The "
         "reversal-vs-continuation distinction the hypothesis proposes is therefore "
         "only partially testable from existing telemetry.\n",
         f"\n**Result:** {hyp['conclusion']}\n"]
    _w("CONTINUATION_VS_REVERSAL.md", "\n".join(t), written)

    # 3. DIRECTION_CHALLENGER_SHADOW.md
    t = [f"# DIRECTION_CHALLENGER_SHADOW.md\n", disclaimer,
         "\n## Pure shadow challenger (veto-only; never reaches the executor)\n",
         "Input = decision-point features. Output = BUY/SELL/HOLD + reason codes. It "
         "keeps the production fade unless a continuation signature fires, then HOLDs. "
         "It can only REMOVE trades — never add or flip one.\n",
         f"\n### Best full-history policy: `{bt.policy}`\n",
         _md_table(["metric", "baseline (keep all)", "challenger (kept)"],
                   [["trades", bt.n_total, bt.n_kept],
                    ["net R", bt.baseline_net_R, bt.kept_net_R],
                    ["profit factor", bt.baseline_PF, bt.kept_PF],
                    ["win rate %", bt.baseline_winrate, bt.kept_winrate],
                    ["max drawdown R", bt.baseline_maxDD_R, bt.kept_maxDD_R]]),
         "\n\n### Rejection accounting\n",
         _md_table(["quantity", "value"],
                   [["trades vetoed", bt.n_vetoed],
                    ["  of which wrong_direction (good vetoes)", bt.vetoed_wrong_direction],
                    ["  of which clean winners (FALSE vetoes)", bt.vetoed_clean_winners],
                    ["  of which other losses", bt.vetoed_other_loss],
                    ["  of which unlabeled", bt.vetoed_no_label],
                    ["losses avoided (R)", bt.losses_avoided_R],
                    ["winners forgone (R)", bt.winners_forgone_R],
                    ["net R retained %", bt.net_R_retained_pct],
                    ["trade-count change", bt.trade_count_change]]),
         "\n\n### By symbol\n",
         _md_table(["symbol", "kept", "vetoed", "kept_net_R", "vetoed_wrong_dir", "vetoed_winners"],
                   [[k, v["n_kept"], v["n_vetoed"], v["kept_net_R"], v["vetoed_wrong_dir"], v["vetoed_winners"]]
                    for k, v in bt.slices["by_symbol"].items()]),
         "\n\n### By direction\n",
         _md_table(["direction", "kept", "vetoed", "kept_net_R", "vetoed_wrong_dir", "vetoed_winners"],
                   [[k, v["n_kept"], v["n_vetoed"], v["kept_net_R"], v["vetoed_wrong_dir"], v["vetoed_winners"]]
                    for k, v in bt.slices["by_direction"].items()]),
         "\n\n### By month\n",
         _md_table(["month", "kept", "vetoed", "kept_net_R", "vetoed_wrong_dir", "vetoed_winners"],
                   [[k, v["n_kept"], v["n_vetoed"], v["kept_net_R"], v["vetoed_wrong_dir"], v["vetoed_winners"]]
                    for k, v in sorted(bt.slices["by_month"].items())]),
         "\n\n### By regime\n",
         _md_table(["regime", "kept", "vetoed", "kept_net_R", "vetoed_wrong_dir", "vetoed_winners"],
                   [[k, v["n_kept"], v["n_vetoed"], v["kept_net_R"], v["vetoed_wrong_dir"], v["vetoed_winners"]]
                    for k, v in bt.slices["by_regime"].items()]),
         "\n"]
    _w("DIRECTION_CHALLENGER_SHADOW.md", "\n".join(t), written)

    # 4. DIRECTION_WALK_FORWARD.md
    t = [f"# DIRECTION_WALK_FORWARD.md\n", disclaimer,
         "\n## Chronological validation — fit on TRAIN only, freeze, evaluate held-out\n",
         "TRAIN 2026-06-15..07-15 · VALIDATION 07-16..07-31 · OOS 08-01..08-18. The "
         "threshold grid is searched ONLY on TRAIN; the single best policy is applied "
         "unchanged to VALIDATION and OOS.\n",
         f"\n**Best TRAIN policy:** `{wf.best_policy}`\n\n"]
    def _wf_rows(d):
        return [d.get("n_total"), d.get("n_kept"), d.get("n_vetoed"),
                d.get("baseline_net_R"), d.get("kept_net_R"), d.get("delta_R"),
                d.get("baseline_PF"), d.get("kept_PF"),
                d.get("vetoed_wrong_direction"), d.get("vetoed_clean_winners")]
    t.append(_md_table(
        ["slice", "n", "kept", "vetoed", "base_R", "kept_R", "ΔR", "base_PF", "kept_PF", "veto_wrongdir", "veto_winners"],
        [["TRAIN"] + _wf_rows(wf.train),
         ["VALIDATION"] + _wf_rows(wf.validation),
         ["OOS (Aug)"] + _wf_rows(wf.oos)]))
    t.append("\n\n### Feature coverage by split (why the above looks the way it does)\n")
    cov = wf.coverage
    t.append(_md_table(
        ["slice", "n", "MFE labels", "displacement_ratio", "sr_level_dist_pips", "regime_confidence"],
        [[name, cov[name]["n"], cov[name]["mfe"], cov[name]["displacement_ratio"],
          cov[name]["sr_level_dist_pips"], cov[name]["regime_confidence"]]
         for name in ("train", "validation", "oos")]))
    t.append(f"\n\n**Generalizes across held-out slices:** {wf.generalizes}\n")
    t.append(f"**Fitted-to-August (helps OOS, hurts validation):** {wf.august_only}\n")
    t.append(f"**Untrainable (discriminating features absent in TRAIN):** {wf.untrainable}\n")
    t.append(f"**Walk-forward verdict:** {wf.verdict}\n")
    t.append("\n> Any rule that only improves OOS/August while hurting VALIDATION is "
             "rejected by design — it is fitted to the single adverse regime, not a "
             "stable direction signal.\n")
    _w("DIRECTION_WALK_FORWARD.md", "\n".join(t), written)

    # 5. DIRECTION_DECISION.md
    t = [f"# DIRECTION_DECISION.md\n", disclaimer,
         f"\n# FINAL DECISION: {dec['label']}\n",
         f"\n{dec['statement']}\n",
         "\n## Evidence summary\n"]
    for b in dec["evidence"]:
        t.append(f"- {b}\n")
    t.append("\n## Why not the other outcomes\n")
    for b in dec["why_not"]:
        t.append(f"- {b}\n")
    t.append("\n## What would change the verdict (evidence to gather, not build)\n")
    for b in dec["to_upgrade"]:
        t.append(f"- {b}\n")
    t.append("\n_No candidate is implemented into production in this phase._\n")
    _w("DIRECTION_DECISION.md", "\n".join(t), written)
    return written
