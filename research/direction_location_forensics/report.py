"""Aggregate verdicts and emit datasets + the five markdown forensic reports.

Writes ONLY under this package (``out/`` for datasets, package root for the .md).
Never writes to reports/ or any production path.
"""

from __future__ import annotations

import os
import csv
import json
import collections
from typing import Any, Dict, List, Optional, Callable

from .loader import ForensicTrade, parse_ts
from .classify import (TradeVerdict, DIR_WORKED_MFE_FRAC, DIR_DEAD_MFE_FRAC,
                       LOC_BAD_MAE_FRAC, TIMING_IMPULSE_HI, EXIT_WINNER_MFE_FRAC,
                       EXIT_CAPTURE_BAD)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(_THIS_DIR, "out")


def _assert_isolated(path: str) -> None:
    ap = os.path.abspath(path).replace("\\", "/").lower()
    if "/reports/signals" in ap:
        raise RuntimeError(f"forensics refuses to write a production journal path: {path}")
    if "/direction_location_forensics/" not in ap:
        raise RuntimeError(f"forensics writes only inside its package, got: {path}")


def _month(t: ForensicTrade) -> str:
    dt = parse_ts(t)
    return dt.strftime("%Y-%m") if dt else "unknown"


def _pips(vs: List[TradeVerdict]) -> float:
    return round(sum(v.pips for v in vs if v.pips is not None), 1)


def _winrate(vs: List[TradeVerdict]) -> Optional[float]:
    dec = [v for v in vs if v.outcome in ("WIN", "LOSS")]
    if not dec:
        return None
    return round(100.0 * sum(v.outcome == "WIN" for v in dec) / len(dec), 1)


def _grp(items, keyfn: Callable) -> Dict[str, list]:
    out = collections.defaultdict(list)
    for it in items:
        out[keyfn(it)].append(it)
    return dict(out)


# ── datasets ─────────────────────────────────────────────────────────────────
def write_datasets(trades: List[ForensicTrade], verdicts: List[TradeVerdict]) -> Dict[str, str]:
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = {}

    trades_json = os.path.join(OUT_DIR, "forensic_trades.json")
    _assert_isolated(trades_json)
    with open(trades_json, "w", encoding="utf-8") as f:
        json.dump([t.to_dict() for t in trades], f, indent=1)
    paths["trades_json"] = trades_json

    verdicts_json = os.path.join(OUT_DIR, "forensic_verdicts.json")
    _assert_isolated(verdicts_json)
    with open(verdicts_json, "w", encoding="utf-8") as f:
        json.dump([v.to_dict() for v in verdicts], f, indent=1)
    paths["verdicts_json"] = verdicts_json

    # joined CSV (trade fields + verdict grades), one row per trade
    csv_path = os.path.join(OUT_DIR, "forensic_dataset.csv")
    _assert_isolated(csv_path)
    vmap = {(v.account, v.ticket): v for v in verdicts}
    tfields = list(ForensicTrade().to_dict().keys())
    tfields.remove("missing")
    vfields = ["direction_grade", "location_grade", "timing_grade", "exit_grade",
               "risk_state_effect", "mfe_frac", "mae_frac", "realized_frac",
               "mfe_capture", "primary_bucket", "confidence"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(tfields + vfields + ["n_missing"])
        for t in trades:
            v = vmap.get((t.account, t.ticket))
            td = t.to_dict()
            row = [td.get(k) for k in tfields]
            row += [getattr(v, k) if v else None for k in vfields]
            row += [len(t.missing)]
            w.writerow(row)
    paths["dataset_csv"] = csv_path
    return paths


# ── shared aggregation for the summary + markdown ────────────────────────────
def bucket_table(verdicts: List[TradeVerdict]) -> List[Dict[str, Any]]:
    by = _grp(verdicts, lambda v: v.primary_bucket)
    order = ["clean_win", "wrong_direction", "bad_location", "bad_timing",
             "bad_exit", "risk_state_distortion", "loss_no_excursion_data",
             "unclassified_loss"]
    rows = []
    total = len(verdicts) or 1
    for b in order + [k for k in by if k not in order]:
        vs = by.get(b, [])
        if not vs:
            continue
        rows.append({"bucket": b, "n": len(vs), "pct": round(100.0 * len(vs) / total, 1),
                     "net_pips": _pips(vs), "win_rate": _winrate(vs)})
    return rows


def summary(all_v: Dict[str, List[TradeVerdict]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"cutpoints": {
        "DIR_WORKED_MFE_FRAC": DIR_WORKED_MFE_FRAC, "DIR_DEAD_MFE_FRAC": DIR_DEAD_MFE_FRAC,
        "LOC_BAD_MAE_FRAC": LOC_BAD_MAE_FRAC, "TIMING_IMPULSE_HI": TIMING_IMPULSE_HI,
        "EXIT_WINNER_MFE_FRAC": EXIT_WINNER_MFE_FRAC, "EXIT_CAPTURE_BAD": EXIT_CAPTURE_BAD}}
    for acct, vs in all_v.items():
        high = [v for v in vs if v.confidence == "high"]
        out[acct] = {
            "n_trades": len(vs), "net_pips": _pips(vs), "win_rate": _winrate(vs),
            "n_high_confidence": len(high),
            "waterfall_all": bucket_table(vs),
            "waterfall_high_confidence": bucket_table(high),
            "by_symbol": {k: {"n": len(g), "net_pips": _pips(g), "win_rate": _winrate(g)}
                          for k, g in _grp(vs, lambda v: v.symbol or "?").items()},
            "by_direction": {k: {"n": len(g), "net_pips": _pips(g), "win_rate": _winrate(g)}
                             for k, g in _grp(vs, lambda v: v.direction or "?").items()},
            "risk_state_effects": dict(collections.Counter(
                v.risk_state_effect for v in vs if v.risk_state_effect)),
        }
    return out


# ── markdown helpers ─────────────────────────────────────────────────────────
def _md_table(headers: List[str], rows: List[list]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    return "\n".join(out)


def _grade_counts(verdicts, attr, order):
    c = collections.Counter(getattr(v, attr) for v in verdicts)
    rows = []
    tot = len(verdicts) or 1
    for g in order + [k for k in c if k not in order]:
        if c.get(g):
            grp = [v for v in verdicts if getattr(v, attr) == g]
            rows.append([g, c[g], f"{100*c[g]/tot:.1f}%", _pips(grp), _winrate(grp)])
    return rows


def write_markdown(all_t: Dict[str, List[ForensicTrade]],
                   all_v: Dict[str, List[TradeVerdict]], summ: Dict[str, Any]) -> List[str]:
    written = []

    def _w(name, text):
        p = os.path.join(_THIS_DIR, name)
        _assert_isolated(p)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        written.append(p)

    lead_v, node_v = all_v.get("lead", []), all_v.get("node", [])
    cut = summ["cutpoints"]
    preamble = (f"> Measurement only — no strategy change proposed. Cut-points (fixed, not "
                f"tuned): MFE-worked ≥{cut['DIR_WORKED_MFE_FRAC']}×stop, MFE-dead "
                f"<{cut['DIR_DEAD_MFE_FRAC']}×stop, MAE-bad ≥{cut['LOC_BAD_MAE_FRAC']}×stop, "
                f"impulse ≥{cut['TIMING_IMPULSE_HI']}, winner ≥{cut['EXIT_WINNER_MFE_FRAC']}×stop, "
                f"give-back capture <{cut['EXIT_CAPTURE_BAD']}. 'high' confidence = MFE/MAE "
                f"present; 'proxy' = realized-only.\n")

    # 1. DIRECTION
    txt = ["# DIRECTION_FORENSIC.md\n", preamble,
           "\n## Q1 — Was the faded direction wrong?\n",
           "Direction grade from favorable excursion (MFE) vs the stop distance. "
           "`wrong` = price barely moved our way before failing; `right` = the fade "
           "had real edge at least intra-trade.\n"]
    for acct, vs in (("LEAD", lead_v), ("NODE", node_v)):
        high = [v for v in vs if v.confidence == "high"]
        txt.append(f"\n### {acct} — direction grade (high-confidence subset, n={len(high)})\n")
        txt.append(_md_table(["grade", "n", "share", "net_pips", "win%"],
                             _grade_counts(high, "direction_grade", ["right", "weak", "wrong", "unknown"])) or "(no MFE/MAE rows)")
        txt.append(f"\n\n_Full population n={len(vs)} (incl. proxy):_\n")
        txt.append(_md_table(["grade", "n", "share", "net_pips", "win%"],
                             _grade_counts(vs, "direction_grade", ["right", "weak", "wrong", "unknown"])))
        txt.append("\n")
    txt.append("\n**Reading:** a large `wrong` share would mean the entry side itself is the "
               "leak. A small `wrong` share with losses concentrated later (location/timing/"
               "exit) means the fade direction is broadly right and the damage is downstream.\n")
    _w("DIRECTION_FORENSIC.md", "\n".join(txt))

    # 2. LOCATION
    txt = ["# LOCATION_FORENSIC.md\n", preamble,
           "\n## Q2 — Right direction, wrong location?\n",
           "Location grade from adverse excursion (MAE) depth vs stop, plus S/R-side "
           "context. `bad` = the trade ran deep against us early (poor entry spot) even "
           "when the direction eventually worked.\n"]
    for acct, vs in (("LEAD", lead_v), ("NODE", node_v)):
        high = [v for v in vs if v.confidence == "high"]
        txt.append(f"\n### {acct} — location grade (high-confidence subset, n={len(high)})\n")
        txt.append(_md_table(["grade", "n", "share", "net_pips", "win%"],
                             _grade_counts(high, "location_grade", ["ok", "bad", "unknown"])) or "(no MFE/MAE rows)")
        txt.append("\n")
    # S/R-type slice (lead)
    sr = _grp([t for t in all_t.get("lead", []) if t.sr_level_type], lambda t: t.sr_level_type)
    if sr:
        txt.append("\n### LEAD — by S/R level type at entry (context; join rate is partial)\n")
        rows = []
        vby = {(v.account, v.ticket): v for v in lead_v}
        for k, ts in sorted(sr.items(), key=lambda kv: -len(kv[1])):
            vs = [vby[("lead", t.ticket)] for t in ts if ("lead", t.ticket) in vby]
            rows.append([k, len(ts), _pips(vs), _winrate(vs)])
        txt.append(_md_table(["sr_level_type", "n", "net_pips", "win%"], rows))
        txt.append("\n")
    _w("LOCATION_FORENSIC.md", "\n".join(txt))

    # 3. TIMING
    txt = ["# ENTRY_TIMING_FORENSIC.md\n", preamble,
           "\n## Q3 — Right direction & location, wrong timing?\n",
           "Timing grade from displacement (did we fade INTO momentum) and MTF bias. "
           "`bad` = faded a strong impulse or against the higher-timeframe bias.\n"]
    for acct, vs in (("LEAD", lead_v), ("NODE", node_v)):
        txt.append(f"\n### {acct} — timing grade (n={len(vs)})\n")
        txt.append(_md_table(["grade", "n", "share", "net_pips", "win%"],
                             _grade_counts(vs, "timing_grade", ["ok", "bad", "unknown"])))
        txt.append("\n")
    # regime slice
    reg = _grp([t for t in all_t.get("lead", []) if t.dominant_regime], lambda t: t.dominant_regime)
    if reg:
        txt.append("\n### LEAD — by dominant regime at entry\n")
        vby = {(v.account, v.ticket): v for v in lead_v}
        rows = [[k, len(ts), _pips([vby[("lead", t.ticket)] for t in ts if ("lead", t.ticket) in vby]),
                 _winrate([vby[("lead", t.ticket)] for t in ts if ("lead", t.ticket) in vby])]
                for k, ts in sorted(reg.items(), key=lambda kv: -len(kv[1]))]
        txt.append(_md_table(["regime", "n", "net_pips", "win%"], rows))
        txt.append("\n")
    _w("ENTRY_TIMING_FORENSIC.md", "\n".join(txt))

    # 4. EXIT
    txt = ["# EXIT_FORENSIC.md\n", preamble,
           "\n## Q4 — Right trade, bad exit?\n",
           "Exit grade compares realized pips to the favorable excursion (MFE). "
           "`gave_back` = a winner-grade move (MFE ≥ stop) captured at <35%.\n"]
    for acct, vs in (("LEAD", lead_v), ("NODE", node_v)):
        high = [v for v in vs if v.confidence == "high"]
        txt.append(f"\n### {acct} — exit grade (high-confidence subset, n={len(high)})\n")
        txt.append(_md_table(["grade", "n", "share", "net_pips", "win%"],
                             _grade_counts(high, "exit_grade", ["ok", "gave_back", "unknown"])) or "(no MFE/MAE rows)")
        caps = [v.mfe_capture for v in high if v.mfe_capture is not None]
        if caps:
            caps.sort()
            txt.append(f"\n\n_MFE capture (realized/MFE) over n={len(caps)}: "
                       f"median {caps[len(caps)//2]*100:.0f}%, "
                       f"min {caps[0]*100:.0f}%, max {caps[-1]*100:.0f}%._\n")
    # exit reason distribution
    for acct, ts in (("LEAD", all_t.get("lead", [])), ("NODE", all_t.get("node", []))):
        rc = collections.Counter(t.exit_reason for t in ts)
        txt.append(f"\n### {acct} — exit reasons\n")
        txt.append(_md_table(["reason", "n"], [[k, v] for k, v in rc.most_common()]))
        txt.append("\n")
    _w("EXIT_FORENSIC.md", "\n".join(txt))

    # 5. WATERFALL
    txt = ["# DIRECTION_LOCATION_WATERFALL.md\n", preamble,
           "\n## The five-stage attribution\n",
           "Each trade is attributed to the FIRST failing stage: **direction → location "
           "→ timing → exit → risk-state**. Winners with a clean exit are `clean_win`. "
           "Losses with no MFE/MAE are `loss_no_excursion_data` (kept separate — never "
           "counted as a measured cause).\n"]
    for acct, vs in (("LEAD", lead_v), ("NODE", node_v)):
        s = summ.get(acct.lower(), {})
        txt.append(f"\n### {acct} — waterfall, FULL population (n={len(vs)}, "
                   f"net {_pips(vs)}p, win {_winrate(vs)}%)\n")
        txt.append(_md_table(["stage", "n", "share", "net_pips", "win%"],
                             [[r["bucket"], r["n"], f"{r['pct']}%", r["net_pips"], r["win_rate"]]
                              for r in s.get("waterfall_all", [])]))
        hi = s.get("waterfall_high_confidence", [])
        txt.append(f"\n\n### {acct} — waterfall, HIGH-CONFIDENCE subset "
                   f"(MFE/MAE present, n={s.get('n_high_confidence', 0)})\n")
        txt.append(_md_table(["stage", "n", "share", "net_pips", "win%"],
                             [[r["bucket"], r["n"], f"{r['pct']}%", r["net_pips"], r["win_rate"]]
                              for r in hi]) if hi else "(no MFE/MAE rows on this account)")
        rse = s.get("risk_state_effects", {})
        if rse:
            txt.append("\n\n**Risk-state effects present (any stage):** "
                       + ", ".join(f"{k}×{v}" for k, v in rse.items()) + "\n")
        txt.append("\n")
    txt.append("\n## How to read this\n"
               "- A dominant `wrong_direction` bar ⇒ the entry side is the leak (fix direction).\n"
               "- A dominant `bad_location`/`bad_timing` bar ⇒ direction is right; the entry "
               "trigger/context is the leak.\n"
               "- A dominant `bad_exit` bar ⇒ the setups are right; the trade management gives "
               "it back.\n"
               "- `risk_state_distortion` quantifies losses driven by forced exits (RiskGuard "
               "breach / EOD flat / retest veto), not the setup itself.\n"
               "\n_No fix is recommended here by design — this file localizes WHERE the money "
               "leaks so the next phase can target it._\n")
    _w("DIRECTION_LOCATION_WATERFALL.md", "\n".join(txt))

    return written
