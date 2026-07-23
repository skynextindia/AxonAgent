"""Read-only inventory of engine snapshot CSVs. Streams; never loads whole files."""
import csv, os, glob, calendar, time, json, math
from collections import defaultdict

REPORTS = "D:/AXON.AI/AxonAgent-Agy/reports"
OUT = "D:/AXON.AI/AxonAgent-Agy/scratch"

EXPECTED = ["timestamp","price","vel_pct","vel_z","vel_decaying","decay_ratio","vol_pips",
"tick_eff","tick_rate_10s","tick_rate_60s","tick_rate_300s","displacement_velocity","abs_velocity",
"velocity_ratio","is_unusual","is_accelerating","disp_class","disp_ratio","net_disp_pips","regime",
"h4_bias","h1_bias","m15_bias","reversal_pressure","is_exhaustion_zone","structure_break",
"active_sweeps","active_breaks","liquidity_void","entry_state","entry_dir","signal_quality",
"skip_reason","at_structure","dist_to_sr","dist_to_liq","room_pips","near_level_type",
"near_level_price","range_pos","range_used"]

BIAS_TOKENS = {"BULLISH","BEARISH","NEUTRAL","BULL","BEAR","LONG","SHORT","UP","DOWN",""}

def pip(sym):
    return 0.01 if ("JPY" in sym or "XAU" in sym) else 0.0001

def parse_ts(s):
    try:
        return calendar.timegm(time.strptime(s.strip(), "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return None

def symbol_of(path):
    b = os.path.basename(path)
    core = b[len("engine_snapshots_"):-len(".csv")]
    return core.split("_")[0]

files = sorted(glob.glob(os.path.join(REPORTS, "engine_snapshots_*.csv")))

per_file = []
# per symbol: epoch -> None (set); plus price series for hazard detection
sym_epochs = defaultdict(set)
sym_prices = defaultdict(dict)   # epoch -> price (first seen wins)
sym_tickrate = defaultdict(dict) # epoch -> tick_rate_60s

for path in files:
    sym = symbol_of(path)
    rows = 0
    first_ts = last_ts = None
    min_ts = None
    max_ts = None
    hdr_ok = None
    entry_state_vals = defaultdict(int)
    numeric_entry_state = 0
    bad_ts = 0
    bad_price = 0
    colcount_mismatch = 0
    monotonic_breaks = 0
    prev_ts = None
    with open(path, "r", newline="", encoding="utf-8", errors="replace") as fh:
        rd = csv.reader(fh)
        try:
            header = next(rd)
        except StopIteration:
            header = []
        hdr_ok = [h.strip() for h in header] == EXPECTED
        idx = {h.strip(): i for i, h in enumerate(header)}
        i_ts = idx.get("timestamp", 0)
        i_px = idx.get("price", 1)
        i_es = idx.get("entry_state", 29)
        i_tr = idx.get("tick_rate_60s", 9)
        ncol = len(header)
        for r in rd:
            if not r:
                continue
            if len(r) != ncol:
                colcount_mismatch += 1
                if len(r) <= max(i_ts, i_px, i_es):
                    continue
            rows += 1
            ts = parse_ts(r[i_ts]) if i_ts < len(r) else None
            if ts is None:
                bad_ts += 1
            else:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
                if min_ts is None or ts < min_ts: min_ts = ts
                if max_ts is None or ts > max_ts: max_ts = ts
                if prev_ts is not None and ts < prev_ts:
                    monotonic_breaks += 1
                prev_ts = ts
            es = r[i_es].strip() if i_es < len(r) else ""
            if len(entry_state_vals) < 40:
                entry_state_vals[es] += 1
            else:
                if es in entry_state_vals:
                    entry_state_vals[es] += 1
            # numeric / bias-like detection
            try:
                float(es)
                numeric_entry_state += 1
            except ValueError:
                pass
            px = None
            try:
                px = float(r[i_px])
            except Exception:
                bad_price += 1
            if ts is not None and px is not None:
                if ts not in sym_prices[sym]:
                    sym_prices[sym][ts] = px
                    try:
                        sym_tickrate[sym][ts] = float(r[i_tr]) if i_tr < len(r) else float("nan")
                    except Exception:
                        sym_tickrate[sym][ts] = float("nan")
                sym_epochs[sym].add(ts)

    per_file.append(dict(
        path=path.replace("\\", "/"), symbol=sym, rows=rows,
        first_ts=first_ts, last_ts=last_ts, min_ts=min_ts, max_ts=max_ts,
        header_exact=hdr_ok, header_len=ncol,
        entry_state_sample=dict(sorted(entry_state_vals.items(), key=lambda kv: -kv[1])[:12]),
        numeric_entry_state=numeric_entry_state,
        bad_ts=bad_ts, bad_price=bad_price, colcount_mismatch=colcount_mismatch,
        monotonic_breaks=monotonic_breaks,
    ))

def fmt(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts)) if ts else None

# ---- per-symbol merged analysis ----
sym_report = {}
for sym, eps in sym_epochs.items():
    ordered = sorted(eps)
    buckets = set((e // 900) * 900 for e in ordered)
    gaps = []
    prev = None
    for e in ordered:
        if prev is not None:
            d = e - prev
            if d > 300:
                gaps.append((prev, e, d))
        prev = e
    gaps.sort(key=lambda g: -g[2])
    # weekend coverage: epochs falling Sat/Sun UTC
    wk = 0
    for e in ordered:
        wd = time.gmtime(e).tm_wday
        if wd == 5 or wd == 6:
            wk += 1
    # tick spacing distribution
    diffs = defaultdict(int)
    prev = None
    for e in ordered:
        if prev is not None:
            diffs[e - prev] += 1
        prev = e
    # synthetic detection heuristics: constant spacing runs + price step uniformity
    px = sym_prices[sym]
    p = pip(sym)
    step_hist = defaultdict(int)
    zero_moves = 0
    prev = None
    for e in ordered:
        if prev is not None:
            d = abs(px[e] - px[prev]) / p
            key = round(d, 1)
            step_hist[key] += 1
            if key == 0.0:
                zero_moves += 1
        prev = e
    sym_report[sym] = dict(
        distinct_epochs=len(ordered),
        first=fmt(ordered[0]), last=fmt(ordered[-1]),
        span_days=round((ordered[-1] - ordered[0]) / 86400.0, 2),
        m15_bars=len(buckets),
        weekend_rows=wk,
        top_gaps=[(fmt(a), fmt(b), d) for a, b, d in gaps[:12]],
        gap_count_gt_5min=len(gaps),
        gap_count_gt_1h=sum(1 for g in gaps if g[2] > 3600),
        spacing_top=dict(sorted(diffs.items(), key=lambda kv: -kv[1])[:10]),
        pip_step_top=dict(sorted(step_hist.items(), key=lambda kv: -kv[1])[:10]),
        zero_move_ticks=zero_moves,
    )

out = dict(files=[dict(f, first_ts=fmt(f["first_ts"]), last_ts=fmt(f["last_ts"]),
                       min_ts=fmt(f["min_ts"]), max_ts=fmt(f["max_ts"])) for f in per_file],
           symbols=sym_report)
with open(os.path.join(OUT, "snapshot_inventory.json"), "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=1, default=str)
print(json.dumps(out, indent=1, default=str))
