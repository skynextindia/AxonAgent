"""Asian-range breakout backtest (READ-ONLY research).

Rule: take the Asian-session high/low as the range. In the London window, the
FIRST break of that range (+buffer) enters in the break direction (long above the
high / short below the low). Fixed stop + target; one trade per day; hold to EOD
if neither hits. Reported PER YEAR (2023-2026) net of cost so an edge must be
consistent, not one lucky stretch.

Sessions use the data's timestamp hour (~London/BST): Asian 0-7, London-open
entry window 7-12, exit by hour 18. Pure calc; writes nothing.
"""
from __future__ import annotations
import csv, os
from collections import OrderedDict, defaultdict

PIP = 0.0001
_CSV = os.path.join(os.path.dirname(__file__), "..", "..", "eurusd_h1_2y.csv")


def load_days():
    days = OrderedDict()
    with open(_CSV) as f:
        for r in csv.DictReader(f):
            try:
                dt = r["Datetime"]
                rec = (dt[:4], int(dt[11:13]), float(r["Open"]), float(r["High"]),
                       float(r["Low"]), float(r["Close"]))
                days.setdefault(dt[:10], []).append(rec)
            except Exception:
                continue
    return days


def backtest(stop_p, target_p, buf=1.0, cost=1.0, max_range=None,
             asia=(0, 7), entry_win=(7, 12), exit_h=18):
    days = load_days()
    per = defaultdict(list)
    for d, bars in days.items():
        yr = bars[0][0]
        A = [x for x in bars if asia[0] <= x[1] < asia[1]]
        if not A:
            continue
        AH = max(x[3] for x in A); AL = min(x[4] for x in A)
        if max_range is not None and (AH - AL) / PIP > max_range:
            continue
        win = [x for x in bars if entry_win[0] <= x[1] < entry_win[1]]
        # find first break in the entry window
        entry = None; side = None; ei = None
        allbars = bars
        for k, x in enumerate(bars):
            if not (entry_win[0] <= x[1] < entry_win[1]):
                continue
            if x[3] >= AH + buf * PIP:
                entry = AH + buf * PIP; side = "L"; ei = k; break
            if x[4] <= AL - buf * PIP:
                entry = AL - buf * PIP; side = "S"; ei = k; break
        if entry is None:
            continue
        stop = entry - stop_p * PIP if side == "L" else entry + stop_p * PIP
        tgt = entry + target_p * PIP if side == "L" else entry - target_p * PIP
        pnl = None
        for x in bars[ei + 1:]:
            if x[1] >= exit_h:
                break
            if side == "L":
                if x[4] <= stop: pnl = -stop_p; break        # adverse-first
                if x[3] >= tgt: pnl = target_p; break
            else:
                if x[3] >= stop: pnl = -stop_p; break
                if x[4] <= tgt: pnl = target_p; break
        if pnl is None:  # exit at last bar before exit_h
            outbars = [x for x in bars[ei + 1:] if x[1] < exit_h]
            ex = outbars[-1][5] if outbars else entry
            pnl = (ex - entry) / PIP if side == "L" else (entry - ex) / PIP
        per[yr].append(pnl - cost)
    return per


def stats(t):
    if not t: return None
    net = sum(t); w = [x for x in t if x > 0]; l = [x for x in t if x <= 0]
    pf = sum(w) / abs(sum(l)) if l and sum(l) != 0 else float("inf")
    # max drawdown on the equity curve
    eq = 0; peak = 0; mdd = 0
    for x in t:
        eq += x; peak = max(peak, eq); mdd = min(mdd, eq - peak)
    return dict(n=len(t), win=len(w) / len(t), net=net, avg=net / len(t), pf=pf, mdd=mdd)


def report(name, stop_p, target_p, **kw):
    per = backtest(stop_p, target_p, **kw)
    print(f"\n=== {name}: stop={stop_p} target={target_p} {kw if kw else ''} ===")
    print(f"{'year':6s} {'n':>4s} {'win%':>5s} {'net':>8s} {'avg':>7s} {'PF':>5s} {'maxDD':>7s}")
    allt = []
    for yr in ("2023", "2024", "2025", "2026"):
        s = stats(per.get(yr, []))
        if not s: continue
        allt += per[yr]
        print(f"{yr:6s} {s['n']:4d} {s['win']:4.0%} {s['net']:+8.0f} {s['avg']:+7.2f} {s['pf']:5.2f} {s['mdd']:7.0f}")
    a = stats(allt)
    if a:
        print(f"{'ALL':6s} {a['n']:4d} {a['win']:4.0%} {a['net']:+8.0f} {a['avg']:+7.2f} {a['pf']:5.2f} {a['mdd']:7.0f}")


if __name__ == "__main__":
    report("Base 20/30", 20, 30)
    report("Tight 15/20", 15, 20)
    report("2:1 20/40", 20, 40)
    report("Hold-EOD (wide stop)", 30, 999)
    report("Filter calm Asia (<40p range) 20/30", 20, 30, max_range=40)
