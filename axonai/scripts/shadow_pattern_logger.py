"""Shadow logger for the chart-pattern overlay signal.

READ-ONLY. Touches nothing in the live trading path. It ingests the engine's own
price snapshots (reports/engine_snapshots_{SYMBOL}.csv, the same stream the live
daemon records), detects classic reversal chart patterns (the /api/patterns
geometry: double top/bottom, triple, H&S, triangles, wedges, rectangle), simulates
a paper trade off each neckline break, and appends the resolved outcome to
reports/shadow_patterns.csv.

Purpose: accumulate an OUT-OF-SAMPLE record of the pattern signal's expectancy
without risking a cent. The 2026-07 in-sample study (~2 weeks, 115 trades) showed
a fixed 1R bracket at +5.76p/trade, positive in both trend and chop, but 80% of
the profit sat in a single week. This tool grows the sample so that claim can be
confirmed or killed before the signal is ever wired into live decisions.

Paper-trade model (identical to the validated backtest):
    entry  = neckline break level
    SL     = structural pattern extreme (defines risk R)
    TP     = measured-move target (MM) AND fixed 1R -- both outcomes logged
    exit   = first touch of SL/TP within OUTW bars, else SCRATCH at entry (-cost)
    cost   = 1.0 pip round-trip (flat)
Only fully-resolved patterns are written (break bar + OUTW window entirely in the
past), so a logged outcome never changes. Dedup is by (pair, type, break_time), so
the tool is idempotent and safe to run on a schedule.

Not modelled here: breakout-entry slippage. Fills are assumed at the neckline with
flat cost. Confirming real fills needs a separate live-fill check; this tool
validates expectancy, not microstructure.

Usage:
    python -m axonai.scripts.shadow_pattern_logger              # ingest + append + short summary
    python -m axonai.scripts.shadow_pattern_logger --live-only  # read only the current file (fast, for cron)
    python -m axonai.scripts.shadow_pattern_logger --report     # print cumulative stats from the CSV, no ingest
"""
import argparse
import calendar
import csv
import datetime
import glob
import os
import time
from collections import defaultdict

REPORTS = "reports"
PAIRS = ["AUDUSD", "EURUSD", "GBPUSD", "USDJPY"]
OUT_CSV = os.path.join(REPORTS, "shadow_patterns.csv")

COST_PIPS = 1.0
LOOK = 40          # bars to find the confirming break
OUTW = 60          # bars after break to resolve the paper trade (~15h on M15)
ER_WIN = 96        # 1 trading day of M15 bars for the efficiency ratio
MEDIAN_ER = 0.093  # provisional trend/chop split from the 2026-07 study; report-time only

FIELDS = [
    "logged_at_utc", "pair", "type", "dir", "break_time_utc",
    "entry", "sl", "tp_mm", "tp_1r",
    "risk_pips", "reward_mm_pips", "er",
    "res_mm", "net_mm_pips", "res_1r", "net_1r_pips", "chart_hit",
]


# --------------------------------------------------------------------------
# Price -> M15 bars
# --------------------------------------------------------------------------

def load_bars(symbol, live_only=False):
    pattern = f"engine_snapshots_{symbol}.csv" if live_only else f"engine_snapshots_{symbol}*.csv"
    paths = glob.glob(os.path.join(REPORTS, pattern))
    if not paths:
        return [], 0.0001
    pip = 0.01 if ("JPY" in symbol or "XAU" in symbol) else 0.0001
    ticks = {}
    for fp in paths:
        with open(fp, "r", encoding="utf-8", errors="ignore", newline="") as f:
            for r in csv.DictReader(f):
                ts = (r.get("timestamp") or "")[:19]
                if len(ts) < 19:
                    continue
                try:
                    ep = calendar.timegm(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
                    p = float(r.get("price") or 0)
                except (ValueError, TypeError):
                    continue
                if p > 0:
                    ticks[ep] = p
    bars = {}
    for ep in sorted(ticks.keys()):
        p = ticks[ep]
        bk = (ep // 900) * 900
        b = bars.get(bk)
        if b is None:
            bars[bk] = [p, p, p, p, bk]     # open, high, low, close, time
        else:
            if p > b[1]:
                b[1] = p
            if p < b[2]:
                b[2] = p
            b[3] = p
    return [bars[k] for k in sorted(bars.keys())], pip


# --------------------------------------------------------------------------
# Detection (mirrors api_server.get_patterns geometry) + paper-trade sim
# --------------------------------------------------------------------------

def _zigzag(S, thr):
    piv = []
    ext = S[0][3]
    exti = 0
    trend = 0
    for i in range(1, len(S)):
        c = S[i][3]
        if trend >= 0:
            if c > ext:
                ext, exti = c, i
            elif ext - c >= thr:
                piv.append((exti, "TOP", S[exti][1]))
                trend, ext, exti = -1, c, i
        else:
            if c < ext:
                ext, exti = c, i
            elif c - ext >= thr:
                piv.append((exti, "BOTTOM", S[exti][2]))
                trend, ext, exti = 1, c, i
    return piv


def _first_break(S, frm, level, down):
    for j in range(frm, min(len(S), frm + LOOK)):
        if (S[j][3] < level) if down else (S[j][3] > level):
            return j
    return None


def _eff_ratio(S, b, win=ER_WIN):
    lo = max(1, b - win)
    denom = sum(abs(S[i][3] - S[i - 1][3]) for i in range(lo + 1, b + 1)) or 1e-12
    return abs(S[b][3] - S[lo][3]) / denom


def _sim(S, b, entry, sl, tp, down, pip):
    """First-touch SL vs TP over OUTW bars; time-stop => scratch at entry."""
    end = min(len(S), b + OUTW)
    for j in range(b, end):
        hi, lo = S[j][1], S[j][2]
        hit_sl = (hi >= sl) if down else (lo <= sl)
        hit_tp = (lo <= tp) if down else (hi >= tp)
        if hit_sl:                      # SL priority when a bar spans both
            return -abs(entry - sl) / pip - COST_PIPS, "loss"
        if hit_tp:
            return abs(entry - tp) / pip - COST_PIPS, "win"
    return -COST_PIPS, "scratch"


def _chart_hit(S, b, level, target, down):
    seg = S[b:b + OUTW] or [S[b]]
    x = min(seg, key=lambda r: r[2])[2] if down else max(seg, key=lambda r: r[1])[1]
    return (x <= target) if down else (x >= target)


def _candidates(piv, S):
    """Yield (type, dir, down, neck, target, sl_extreme, break_from_bar) tuples."""
    for i in range(len(piv)):
        w3, w4, w5 = piv[i:i + 3], piv[i:i + 4], piv[i:i + 5]
        if len(w3) == 3:
            a, b, c = w3
            if a[1] == "TOP" and c[1] == "TOP":
                h = (a[2] + c[2]) / 2 - b[2]
                if h > 0 and abs(a[2] - c[2]) <= 0.4 * h:
                    yield ("double_top", "SELL", True, b[2], b[2] - h, max(a[2], c[2]), c[0])
            elif a[1] == "BOTTOM" and c[1] == "BOTTOM":
                h = b[2] - (a[2] + c[2]) / 2
                if h > 0 and abs(a[2] - c[2]) <= 0.4 * h:
                    yield ("double_bottom", "BUY", False, b[2], b[2] + h, min(a[2], c[2]), c[0])
        if len(w5) == 5:
            p = [x[2] for x in w5]
            if w5[0][1] == "TOP":
                t1, b1, t2, b2, t3 = p
                neck = (b1 + b2) / 2
                span = max(t1, t2, t3) - neck
                if span > 0:
                    if max(abs(t1 - t2), abs(t2 - t3), abs(t1 - t3)) <= 0.3 * span:
                        yield ("triple_top", "SELL", True, neck, neck - span, max(t1, t2, t3), w5[4][0])
                    elif t2 > t1 and t2 > t3 and abs(t1 - t3) <= 0.35 * (t2 - neck):
                        yield ("head_shoulders", "SELL", True, neck, neck - (t2 - neck), t2, w5[4][0])
            else:
                b1, t1, b2, t2, b3 = p
                neck = (t1 + t2) / 2
                span = neck - min(b1, b2, b3)
                if span > 0:
                    if max(abs(b1 - b2), abs(b2 - b3), abs(b1 - b3)) <= 0.3 * span:
                        yield ("triple_bottom", "BUY", False, neck, neck + span, min(b1, b2, b3), w5[4][0])
                    elif b2 < b1 and b2 < b3 and abs(b1 - b3) <= 0.35 * (neck - b2):
                        yield ("inv_head_shoulders", "BUY", False, neck, neck + (neck - b2), b2, w5[4][0])
        if (len(w4) == 4 and w4[0][1] != w4[1][1]
                and w4[0][1] == w4[2][1] and w4[1][1] == w4[3][1]):
            pr = [x[2] for x in w4]
            span = max(pr) - min(pr)
            last = w4[3][0]
            if span > 0:
                if w4[0][1] == "TOP":
                    hi1, hi2, lo1, lo2 = pr[0], pr[2], pr[1], pr[3]
                else:
                    lo1, lo2, hi1, hi2 = pr[0], pr[2], pr[1], pr[3]
                flat = 0.12 * span
                dh, dl = hi2 - hi1, lo2 - lo1
                if abs(dh) < flat and abs(dl) < flat:
                    bidx = _first_break(S, last, hi2, False) or _first_break(S, last, lo2, True)
                    if bidx is not None:
                        down = S[bidx][3] < lo2
                        lvl = lo2 if down else hi2
                        yield ("rectangle", "SELL" if down else "BUY", down, lvl,
                               lvl - span if down else lvl + span, max(pr) if down else min(pr), last)
                elif abs(dh) < flat and dl > flat:
                    yield ("asc_triangle", "BUY", False, hi2, hi2 + span, min(pr), last)
                elif abs(dl) < flat and dh < -flat:
                    yield ("desc_triangle", "SELL", True, lo2, lo2 - span, max(pr), last)
                elif dh < -flat and dl > flat:
                    bidx = _first_break(S, last, hi2, False) or _first_break(S, last, lo2, True)
                    if bidx is not None:
                        down = S[bidx][3] < lo2
                        lvl = lo2 if down else hi2
                        yield ("sym_triangle", "SELL" if down else "BUY", down, lvl,
                               lvl - span if down else lvl + span, max(pr) if down else min(pr), last)
                elif dh > flat and dl > flat and dl > dh:
                    yield ("rising_wedge", "SELL", True, lo2, lo2 - span, max(pr), last)
                elif dh < -flat and dl < -flat and abs(dh) > abs(dl):
                    yield ("falling_wedge", "BUY", False, hi2, hi2 + span, min(pr), last)


def resolved_trades(symbol, live_only=False):
    """Return a list of fully-resolved paper trades for one symbol."""
    S, pip = load_bars(symbol, live_only=live_only)
    if len(S) < 20:
        return []
    thr = {"USDJPY": 12.0}.get(symbol, 8.0) * pip
    piv = _zigzag(S, thr)
    last_idx = len(S) - 1
    trades = []
    seen = set()
    for typ, dr, down, neck, target, sl, frm in _candidates(piv, S):
        b = _first_break(S, frm, neck, down)
        if b is None:
            continue
        # only log once the full resolution window exists -> outcome is final
        if b + OUTW > last_idx:
            continue
        entry = neck
        risk_price = abs(entry - sl)
        risk = risk_price / pip
        reward = abs(entry - target) / pip
        if risk < 0.5 or reward < 0.5:
            continue
        key = (typ, S[b][4])
        if key in seen:
            continue
        seen.add(key)
        tp_1r = entry - risk_price if down else entry + risk_price
        net_mm, res_mm = _sim(S, b, entry, sl, target, down, pip)
        net_1r, res_1r = _sim(S, b, entry, sl, tp_1r, down, pip)
        trades.append({
            "pair": symbol, "type": typ, "dir": dr,
            "break_time_utc": datetime.datetime.fromtimestamp(S[b][4], datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "break_epoch": S[b][4],
            "entry": round(entry, 5), "sl": round(sl, 5),
            "tp_mm": round(target, 5), "tp_1r": round(tp_1r, 5),
            "risk_pips": round(risk, 1), "reward_mm_pips": round(reward, 1),
            "er": round(_eff_ratio(S, b), 4),
            "res_mm": res_mm, "net_mm_pips": round(net_mm, 1),
            "res_1r": res_1r, "net_1r_pips": round(net_1r, 1),
            "chart_hit": int(_chart_hit(S, b, entry, target, down)),
        })
    return trades


# --------------------------------------------------------------------------
# CSV persistence (append-only, dedup by pair+type+break_time)
# --------------------------------------------------------------------------

def _existing_keys(path):
    keys = set()
    if not os.path.exists(path):
        return keys
    with open(path, "r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            keys.add((r.get("pair"), r.get("type"), r.get("break_time_utc")))
    return keys


def ingest(live_only=False):
    os.makedirs(REPORTS, exist_ok=True)
    have = _existing_keys(OUT_CSV)
    new_file = not os.path.exists(OUT_CSV)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    added = 0
    skipped_dup = 0
    with open(OUT_CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        for sym in PAIRS:
            for t in resolved_trades(sym, live_only=live_only):
                k = (t["pair"], t["type"], t["break_time_utc"])
                if k in have:
                    skipped_dup += 1
                    continue
                have.add(k)
                t["logged_at_utc"] = now
                w.writerow(t)
                added += 1
    return added, skipped_dup


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def _pct(a, b):
    return 100.0 * a / b if b else 0.0


def _load_log():
    rows = []
    if not os.path.exists(OUT_CSV):
        return rows
    with open(OUT_CSV, "r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                r["net_mm_pips"] = float(r["net_mm_pips"])
                r["net_1r_pips"] = float(r["net_1r_pips"])
                r["er"] = float(r["er"])
            except (ValueError, KeyError, TypeError):
                continue
            rows.append(r)
    return rows


def _agg(rows, col, res_col):
    n = len(rows)
    if not n:
        return None
    win = sum(1 for r in rows if r.get(res_col) == "win")
    loss = sum(1 for r in rows if r.get(res_col) == "loss")
    scr = sum(1 for r in rows if r.get(res_col) == "scratch")
    tot = sum(r[col] for r in rows)
    return n, _pct(win, n), _pct(loss, n), _pct(scr, n), tot / n, tot


def report():
    rows = _load_log()
    if not rows:
        print("shadow_patterns.csv empty or missing -- run ingest first.")
        return
    print("=" * 74)
    print(f"SHADOW PATTERN LOG -- {len(rows)} resolved paper trades")
    print(f"file: {OUT_CSV}")
    print("=" * 74)
    for mode, col, res in (("MM", "net_mm_pips", "res_mm"), ("1R", "net_1r_pips", "res_1r")):
        r = _agg(rows, col, res)
        n, w, l, s, e, t = r
        print(f"[{mode}] n={n}  win={w:.1f}%  loss={l:.1f}%  scr={s:.1f}%  exp={e:+.2f}p/trd  total={t:+.0f}p")
    print("\nper pair (1R):")
    bypair = defaultdict(list)
    for r in rows:
        bypair[r["pair"]].append(r)
    for sym in PAIRS:
        if sym in bypair:
            n, w, l, s, e, t = _agg(bypair[sym], "net_1r_pips", "res_1r")
            print(f"  {sym:7} n={n:>4}  win={w:>5.1f}%  exp={e:+.2f}p  total={t:+.0f}p")
    trend = [r for r in rows if r["er"] >= MEDIAN_ER]
    chop = [r for r in rows if r["er"] < MEDIAN_ER]
    print(f"\nregime (1R, provisional ER split at {MEDIAN_ER}):")
    for name, g in (("TREND", trend), ("CHOP", chop)):
        if g:
            n, w, l, s, e, t = _agg(g, "net_1r_pips", "res_1r")
            print(f"  {name:6} n={n:>4}  win={w:>5.1f}%  exp={e:+.2f}p  total={t:+.0f}p")


def main():
    ap = argparse.ArgumentParser(description="Shadow-log the chart-pattern signal (read-only).")
    ap.add_argument("--live-only", action="store_true",
                    help="read only engine_snapshots_{SYMBOL}.csv (skip archives) for fast incremental runs")
    ap.add_argument("--report", action="store_true",
                    help="print cumulative stats from the CSV and exit (no ingest)")
    args = ap.parse_args()

    if args.report:
        report()
        return

    added, dup = ingest(live_only=args.live_only)
    total = len(_load_log())
    print(f"shadow ingest: +{added} new resolved trades ({dup} already logged). "
          f"cumulative {total} in {OUT_CSV}")
    if added or total:
        print("run with --report for cumulative expectancy.")


if __name__ == "__main__":
    main()
