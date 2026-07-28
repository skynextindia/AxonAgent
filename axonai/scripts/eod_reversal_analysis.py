"""EOD reversal analysis over the daemon-processed engine-snapshot store.

Reads reports/engine_snapshots_{symbol}.csv (written live by the daemon), tags
MAJOR reversals in the price series (>= threshold pips retrace from a running
extreme), and for each one dumps the engine metrics + market-state context in the
run-up to the turn. Also emits reports/calibration_params_{symbol}.json with
conservatively-derived per-pair gate thresholds that the daemon loads at startup.

Usage:
    python -m axonai.scripts.eod_reversal_analysis --symbol XAUUSD
    python -m axonai.scripts.eod_reversal_analysis --symbol EURUSD --reversal-pips 15 --pre 10
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
from typing import List, Dict, Optional


def _pip_mult(symbol: str) -> float:
    s = symbol.upper()
    return 0.01 if ("JPY" in s or "XAU" in s) else 0.0001


def _default_reversal_pips(symbol: str) -> float:
    """Per-pair 'major reversal' size so the daily run needs no manual tuning.
    Gold swings hundreds of pips, JPY mid-range, other FX small. A fixed 15-pip
    default found 0 reversals on low-range pairs (AUDUSD/GBPUSD) and so never
    calibrated them — this scales the threshold to each pair's typical range."""
    s = symbol.upper()
    if "XAU" in s:
        return 120.0
    if "JPY" in s:
        return 12.0
    return 8.0


def _load_rows(path: str) -> List[Dict]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _f(row: Dict, key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def find_reversals(rows: List[Dict], pip: float, reversal_pips: float) -> List[Dict]:
    """Return reversal events: {idx, kind (TOP/BOTTOM), price}. Retrospective."""
    events: List[Dict] = []
    if len(rows) < 3:
        return events
    thresh = reversal_pips * pip
    ext_price = _f(rows[0], "price")
    ext_idx = 0
    direction = 0  # +1 rising toward a TOP, -1 falling toward a BOTTOM
    for i in range(1, len(rows)):
        p = _f(rows[i], "price")
        if direction >= 0 and p > ext_price:
            ext_price, ext_idx = p, i
            direction = 1
        elif direction <= 0 and p < ext_price:
            ext_price, ext_idx = p, i
            direction = -1
        # Reversal confirmed when price retraces >= thresh off the extreme
        if direction == 1 and (ext_price - p) >= thresh:
            events.append({"idx": ext_idx, "kind": "TOP", "price": ext_price})
            ext_price, ext_idx, direction = p, i, -1
        elif direction == -1 and (p - ext_price) >= thresh:
            events.append({"idx": ext_idx, "kind": "BOTTOM", "price": ext_price})
            ext_price, ext_idx, direction = p, i, 1
    return events


def _median(vals: List[float]) -> float:
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else 0.0


def _quantile(vals: List[float], q: float) -> float:
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return 0.0
    # Clamp: int(len*1.0) indexes one past the end.
    return vals[max(0, min(len(vals) - 1, int(len(vals) * q)))]


# Minimum share of live confluence-passed triggers each floor must still admit.
# The floor is derived from PRE-REVERSAL ticks but applied to TRIGGERED ticks,
# and those two distributions are not the same population: measured
# 2026-07-27..28, calibration had pushed EURUSD vel_pct 35 -> 60 and tick_eff
# 0.25 -> 0.48, and only 3 of 162 live triggers (1.9%) survived the full gate
# stack. Each refit tightened further, because a floor that blocks triggers
# cannot produce the trades that would argue for loosening it. Capping the floor
# at a quantile of what live triggers actually produce breaks that ratchet.
DEFAULT_MIN_TRIGGER_PASS = 0.60
# Below this many confluence-passed triggers the cap quantile is too noisy to
# trust; leave the raw floor uncapped rather than loosen it on 3 samples.
MIN_TRIGGER_SAMPLE = 20


def _trigger_feature_pool(rows: List[Dict], keys: List[str]) -> Dict[str, List[float]]:
    """Feature values at the ticks the reversal-edge gate actually judges.

    Population = TRIGGERED rows that PASSED the confluence gate, since the edge
    gate only runs downstream of it. A blank skip_reason means it passed; an
    "edge_gate: ..." skip_reason (written by the daemon's snapshot recorder)
    also passed confluence and was killed by this very gate, so those rows must
    stay in the pool — excluding them would re-create the ratchet.
    """
    pool: Dict[str, List[float]] = {k: [] for k in keys}
    for r in rows:
        if (r.get("entry_state") or "") != "TRIGGERED":
            continue
        sr = (r.get("skip_reason") or "").strip()
        if sr and not sr.startswith("edge_gate:"):
            continue
        for k in keys:
            pool[k].append(_f(r, k))
    return pool


def analyze(symbol: str, reversal_pips: float, pre: int, min_events: int,
            min_trigger_pass: float = DEFAULT_MIN_TRIGGER_PASS,
            min_trigger_sample: int = MIN_TRIGGER_SAMPLE) -> None:
    pip = _pip_mult(symbol)
    store = os.path.join("reports", f"engine_snapshots_{symbol}.csv")
    if not os.path.exists(store):
        print(f"[eod] no snapshot store at {store} — run the daemon first.")
        return
    rows = _load_rows(store)
    events = find_reversals(rows, pip, reversal_pips)
    print(f"[eod] {symbol}: {len(rows)} snapshots, {len(events)} major reversals (>= {reversal_pips} pips)")

    # Dump pre-reversal context windows
    ctx_path = os.path.join("reports", f"reversal_context_{symbol}.csv")
    pre_rows: List[Dict] = []
    feat = {"vel_pct": [], "decay_ratio": [], "reversal_pressure": [], "disp_ratio": [], "vel_z": [], "vol_pips": [], "tick_eff": []}
    with open(ctx_path, "w", encoding="utf-8", newline="") as f:
        writer: Optional[csv.DictWriter] = None
        for e in events:
            lo = max(0, e["idx"] - pre)
            for j in range(lo, e["idx"] + 1):
                r = {k: v for k, v in rows[j].items() if k is not None}
                r["_event_kind"] = e["kind"]
                r["_ticks_to_turn"] = e["idx"] - j
                if writer is None:
                    writer = csv.DictWriter(f, fieldnames=list(r.keys()))
                    writer.writeheader()
                writer.writerow(r)
                pre_rows.append(r)
                # Sample the row nearest the turn for threshold derivation
                if e["idx"] - j <= 2:
                    for k in feat:
                        feat[k].append(_f(rows[j], k))
    print(f"[eod] wrote pre-reversal context -> {ctx_path} ({len(pre_rows)} rows)")

    # Derive the REAL per-pair reversal floors from the observed reversals.
    # Feeds the LIVE reversal-edge gate (config reversal_pair_floors). Replaces
    # the old gate_reversal_pressure_min, which tuned a near-dead signal.
    params: Dict = {}
    if len(events) >= min_events:
        is_gold = "XAU" in symbol.upper()
        # Raw floor = 80% of the observed reversal median: selective (keeps out
        # the chop the system over-trades) yet adaptive per pair/day. Gold has no
        # clean velocity edge → vol-only.
        raw = {
            "vel_pct": 0 if is_gold else round(_median(feat["vel_pct"]) * 0.8, 0),
            "vol_pips": round(_median(feat["vol_pips"]) * 0.8, 2),
            "tick_eff": round(_median(feat["tick_eff"]) * 0.8, 2),
        }
        # Then cap each floor so it still admits at least `min_trigger_pass` of
        # the live triggers this gate is applied to. Without the cap the two
        # populations drift apart and the gate silently closes (see
        # DEFAULT_MIN_TRIGGER_PASS). min() means this can only ever LOOSEN the raw
        # floor, never tighten it — so it cannot re-open the over-trading.
        pool = _trigger_feature_pool(rows, ["vel_pct", "vol_pips", "tick_eff"])
        n_trig = len(pool["vel_pct"])
        floors = dict(raw)
        caps: Dict[str, float] = {}
        if n_trig >= min_trigger_sample:
            qq = max(0.0, min(1.0, 1.0 - min_trigger_pass))
            for k, nd in (("vel_pct", 0), ("vol_pips", 2), ("tick_eff", 2)):
                if is_gold and k == "vel_pct":
                    continue  # gold floor is pinned at 0 by design
                caps[k] = round(_quantile(pool[k], qq), nd)
                floors[k] = min(raw[k], caps[k])
        else:
            print(f"[eod] {symbol}: only {n_trig} confluence-passed triggers "
                  f"(< {min_trigger_sample}); floors left uncapped this run.")
        params["reversal_pair_floors"] = {symbol: floors}
        # Cap audit + modeled admit-rate: the numbers to watch. If
        # _modeled_trigger_pass_rate collapses again, the gate is re-closing.
        params["_floor_raw"] = raw
        params["_floor_trigger_cap"] = caps
        params["_floor_capped"] = {k: bool(k in caps and caps[k] < raw[k]) for k in raw}
        params["_trigger_sample"] = n_trig
        params["_min_trigger_pass"] = min_trigger_pass
        if n_trig:
            ok = sum(
                1 for i in range(n_trig)
                if pool["vel_pct"][i] >= floors["vel_pct"]
                and pool["vol_pips"][i] >= floors["vol_pips"]
                and pool["tick_eff"][i] >= floors["tick_eff"]
            )
            params["_modeled_trigger_pass_rate"] = round(ok / n_trig, 3)
        # Observed medians for transparency (not consumed as thresholds)
        params["_observed_pre_reversal_medians"] = {
            "vel_pct": round(_median(feat["vel_pct"]), 2),
            "vol_pips": round(_median(feat["vol_pips"]), 2),
            "tick_eff": round(_median(feat["tick_eff"]), 3),
            "decay_ratio": round(_median(feat["decay_ratio"]), 3),
            "reversal_pressure": round(_median(feat["reversal_pressure"]), 3),
            "disp_ratio": round(_median(feat["disp_ratio"]), 3),
        }
        params["_events"] = len(events)
    else:
        print(f"[eod] only {len(events)} events (< {min_events}); emitting empty params (defaults kept).")

    out = os.path.join("reports", f"calibration_params_{symbol}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2)
    print(f"[eod] wrote calibration params -> {out}: {params}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--reversal-pips", type=float, default=None,
                    help="retrace (pips) defining a MAJOR reversal; default auto-scales per symbol")
    ap.add_argument("--pre", type=int, default=10, help="snapshot rows before the turn to capture")
    ap.add_argument("--min-events", type=int, default=8, help="min reversals before deriving thresholds")
    args = ap.parse_args()
    rev_pips = args.reversal_pips if args.reversal_pips is not None else _default_reversal_pips(args.symbol)
    analyze(args.symbol, rev_pips, args.pre, args.min_events)


if __name__ == "__main__":
    main()
