"""Read-only inventory v2. Handles fractional-second timestamps and mixed row widths."""
import csv, os, glob, calendar, time, json
from collections import defaultdict, Counter

REPORTS = "D:/AXON.AI/AxonAgent-Agy/reports"
OUT = "D:/AXON.AI/AxonAgent-Agy/scratch"

S41 = ["timestamp","price","vel_pct","vel_z","vel_decaying","decay_ratio","vol_pips",
"tick_eff","tick_rate_10s","tick_rate_60s","tick_rate_300s","displacement_velocity","abs_velocity",
"velocity_ratio","is_unusual","is_accelerating","disp_class","disp_ratio","net_disp_pips","regime",
"h4_bias","h1_bias","m15_bias","reversal_pressure","is_exhaustion_zone","structure_break",
"active_sweeps","active_breaks","liquidity_void","entry_state","entry_dir","signal_quality",
"skip_reason","at_structure","dist_to_sr","dist_to_liq","room_pips","near_level_type",
"near_level_price","range_pos","range_used"]
S39 = S41[:39]                      # no range_pos / range_used
S33 = S41[:33]                      # ends at skip_reason (no location block)
S25 = [c for c in S33 if c not in {"tick_rate_10s","tick_rate_60s","tick_rate_300s",
       "displacement_velocity","abs_velocity","velocity_ratio","is_unusual","is_accelerating"}]
SCHEMAS = {41: S41, 39: S39, 33: S33, 25: S25}
VALID_STATES = {"IDLE","ARMING","TRIGGERED","INVALIDATED","ANOMALY","RETEST_WAIT"}

def pip(sym): return 0.01 if ("JPY" in sym or "XAU" in sym) else 0.0001
def symbol_of(p):
    return os.path.basename(p)[len("engine_snapshots_"):-len(".csv")].split("_")[0]

def parse_ts(s):
    s = s.strip()
    if not s: return None
    base, _, frac = s.partition(".")
    try:
        e = calendar.timegm(time.strptime(base, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return None
    if frac:
        try: e += float("0." + frac)
        except Exception: pass
    return e

def fmt(ts): return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts)) if ts is not None else None

files = sorted(glob.glob(os.path.join(REPORTS, "engine_snapshots_*.csv")))
by_symbol = defaultdict(list)
for p in files: by_symbol[symbol_of(p)].append(p.replace("\\", "/"))

file_reports, sym_reports = [], {}

for sym in sorted(by_symbol):
    merged = {}        # int-second epoch -> price   (dedup key per brief)
    exact = set()      # full float epoch (sub-second) for true-tick counting
    dup_sec = 0
    for path in sorted(by_symbol[sym]):
        rows = 0; ts_min = ts_max = None; first_ts = last_ts = None
        widths = Counter(); bad_ts = 0; bad_px = 0
        es_ok = 0; es_numeric = 0; es_other = 0
        back_jumps = 0; prev = None
        subsec = 0
        with open(path, "r", newline="", encoding="utf-8", errors="replace") as fh:
            rd = csv.reader(fh)
            header = next(rd, [])
            hdr_w = len(header)
            for r in rd:
                if not r: continue
                w = len(r); widths[w] += 1; rows += 1
                sch = SCHEMAS.get(w)
                if sch is None:
                    es_other += 1; continue
                i_es = sch.index("entry_state")
                ts = parse_ts(r[0]); px = None
                try: px = float(r[1])
                except Exception: bad_px += 1
                es = r[i_es].strip()
                if es in VALID_STATES: es_ok += 1
                else:
                    try: float(es); es_numeric += 1
                    except ValueError: es_other += 1
                if ts is None:
                    bad_ts += 1; continue
                if first_ts is None: first_ts = ts
                last_ts = ts
                ts_min = ts if ts_min is None or ts < ts_min else ts_min
                ts_max = ts if ts_max is None or ts > ts_max else ts_max
                if prev is not None and ts < prev: back_jumps += 1
                prev = ts
                if ts != int(ts): subsec += 1
                exact.add(ts)
                sec = int(ts)
                if px is not None:
                    if sec in merged: dup_sec += 1
                    else: merged[sec] = px
        # naive-read drift: what a reader using the FILE HEADER positions would see
        naive_i = header.index("entry_state") if "entry_state" in header else None
        file_reports.append(dict(
            path=path, symbol=sym, rows=rows, header_width=hdr_w,
            row_widths=dict(widths),
            first_ts=fmt(first_ts), last_ts=fmt(last_ts),
            min_ts=fmt(ts_min), max_ts=fmt(ts_max),
            schema_ok=(len(widths) == 1 and hdr_w in SCHEMAS and list(widths)[0] == hdr_w),
            entry_state_valid=es_ok, entry_state_numeric=es_numeric, entry_state_other=es_other,
            bad_ts=bad_ts, bad_price=bad_px, backward_time_jumps=back_jumps,
            subsecond_rows=subsec, naive_entry_state_index=naive_i,
        ))

    # ---- merged per-symbol ----
    ordered = sorted(merged)
    p = pip(sym)
    buckets = set((e // 900) * 900 for e in ordered)
    gaps = []
    diffs = Counter(); steps = Counter(); zero = 0
    prv = None
    for e in ordered:
        if prv is not None:
            d = e - prv
            diffs[d if d <= 10 else (">10s" if d <= 300 else ">300s")] += 1
            if d > 300: gaps.append((prv, e, d))
            mv = round(abs(merged[e] - merged[prv]) / p, 1)
            steps[mv] += 1
            if mv == 0.0: zero += 1
        prv = e
    gaps.sort(key=lambda g: -g[2])
    wk = sum(1 for e in ordered if time.gmtime(e).tm_wday >= 5)
    wk_buckets = sorted(set((e // 900) * 900 for e in ordered if time.gmtime(e).tm_wday >= 5))
    sym_reports[sym] = dict(
        files=len(by_symbol[sym]),
        raw_rows=sum(f["rows"] for f in file_reports if f["symbol"] == sym),
        distinct_subsecond_ticks=len(exact),
        distinct_second_epochs=len(ordered),
        duplicate_second_collisions=dup_sec,
        first=fmt(ordered[0]), last=fmt(ordered[-1]),
        span_days=round((ordered[-1] - ordered[0]) / 86400.0, 2),
        m15_bars=len(buckets),
        coverage_pct=round(100.0 * len(buckets) / max(1, ((ordered[-1] - ordered[0]) // 900 + 1)), 1),
        gaps_gt_5min=len(gaps),
        gaps_gt_1h=sum(1 for g in gaps if g[2] > 3600),
        top_gaps=[(fmt(a), fmt(b), round(d / 3600.0, 2)) for a, b, d in gaps[:10]],
        weekend_second_epochs=wk, weekend_m15_bars=len(wk_buckets),
        weekend_first=fmt(wk_buckets[0]) if wk_buckets else None,
        weekend_last=fmt(wk_buckets[-1]) if wk_buckets else None,
        spacing_hist=dict(sorted(diffs.items(), key=lambda kv: -kv[1])[:8]),
        pip_step_hist=dict(sorted(steps.items(), key=lambda kv: -kv[1])[:8]),
        zero_move_ticks=zero,
    )

out = dict(files=file_reports, symbols=sym_reports)
with open(os.path.join(OUT, "snapshot_inventory.json"), "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=1, default=str)
print(json.dumps(out, indent=1, default=str))
