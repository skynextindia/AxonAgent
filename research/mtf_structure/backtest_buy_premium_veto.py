"""Backtest the MTF buy-at-premium veto: block a BUY when the intraday premium/discount
read (the live mtf_stamp `intraday_pos`) is >= mtf_location_buy_premium_pos (60).

Read-only. Reconstructs the MTF stack AS OF each historical BUY entry (entry_time =
close - hold_seconds) using daily+hourly(+m15/m5 where covered) bars that closed BEFORE
entry (no lookahead) — the SAME plan as daemon._compute_mtf_stamp — then compares the
P&L of BUYs that would be BLOCKED (intraday_pos >= thr) vs KEPT.

    python -m research.mtf_structure.backtest_buy_premium_veto
"""
from __future__ import annotations
import csv, json, os
from datetime import datetime, timedelta, timezone
from .structure import classify_tf, MTFSnapshot

PIP = 0.0001
THR = 60.0
_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_THIS, "..", "..")
SIGNALS = os.path.join(_ROOT, "reports", "signals.jsonl")


def _load(fn, hourly):
    rows = []
    p = os.path.join(_ROOT, fn)
    if not os.path.exists(p):
        return rows
    with open(p) as f:
        for r in csv.DictReader(f):
            t = r.get("Datetime") or r.get("Date")
            try:
                H = float(r["High"]); L = float(r["Low"]); C = float(r["Close"])
                dt = datetime.fromisoformat(t.replace(" ", "T"))
                dt = dt.astimezone(timezone.utc) if hourly else dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            rows.append((dt, H, L, C))
    rows.sort(key=lambda x: x[0])
    return rows


def _before(rows, cut):
    H = []; L = []; C = []
    for dt, h, l, c in rows:
        if dt < cut:
            H.append(h); L.append(l); C.append(c)
        else:
            break
    return H, L, C


def _intraday_pos(daily, hourly, m15, m5, entry_t, cur):
    dcut = entry_t.replace(hour=0, minute=0, second=0, microsecond=0)
    dH, dL, dC = _before(daily, dcut)
    hH, hL, hC = _before(hourly, entry_t)
    mH, mL, mC = _before(m15, entry_t)
    fH, fL, fC = _before(m5, entry_t)
    plan = [("5Y", dH, dL, dC, 1260), ("1Y", dH, dL, dC, 252), ("3M", dH, dL, dC, 63),
            ("1M", dH, dL, dC, 21), ("1W", dH, dL, dC, 5),
            ("1D", hH, hL, hC, 24), ("1H", hH, hL, hC, 12),
            ("15M", mH, mL, mC, 24), ("5M", fH, fL, fC, 24)]
    tfs = []
    for name, H, L, C, bars in plan:
        tf = classify_tf(name, H, L, C, cur, PIP, bars)
        if tf is not None:
            tfs.append(tf)
    intr = [t for t in tfs if t.name in ("1D", "1H", "15M", "5M")]
    if not intr:
        return None, 0
    snap = MTFSnapshot(price=round(cur, 5), tfs=tfs)
    return snap.premium_discount()["intraday_pos"], len(intr)


def main() -> int:
    daily = _load("eurusd_daily_10y.csv", False)
    hourly = _load("eurusd_h1_2y.csv", True)
    m15 = _load("eurusd_m15_recent60d.csv", True)
    m5 = _load("eurusd_5m.csv", True)
    print(f"data: daily={len(daily)} hourly={len(hourly)} m15={len(m15)} m5={len(m5)}")

    buys = []
    with open(SIGNALS) as f:
        for line in f:
            if '"trade_closed"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("direction") != "BUY" or d.get("type") != "trade_closed":
                continue
            buys.append(d)
    print(f"BUY trades in journal: {len(buys)}")

    rec = []  # (pips, profit, intraday_pos, n_intr_frames)
    skipped = 0
    for d in buys:
        try:
            ct = datetime.fromisoformat(d["timestamp_utc"].replace("Z", "+00:00"))
            et = ct - timedelta(seconds=float(d.get("hold_seconds", 0) or 0))
            cur = float(d["entry_price"]); pips = float(d.get("pips", 0)); prof = float(d.get("profit", 0))
        except Exception:
            skipped += 1; continue
        pos, nfr = _intraday_pos(daily, hourly, m15, m5, et, cur)
        if pos is None:
            skipped += 1; continue
        rec.append((pips, prof, pos, nfr))
    print(f"reconstructed {len(rec)} buys (skipped {skipped})\n")

    if not rec:
        print("no reconstructable buys"); return 0

    block = [r for r in rec if r[2] >= THR]
    keep = [r for r in rec if r[2] < THR]

    def stat(rows):
        n = len(rows)
        if n == 0: return (0, 0.0, 0.0, 0.0)
        w = sum(1 for p, _, _, _ in rows if p > 0)
        return (n, w / n * 100, sum(p for p, _, _, _ in rows) / n, sum(pr for _, pr, _, _ in rows))

    na, wa, aa, pa = stat(rec)
    nb, wb, ab, pb = stat(block)
    nk, wk, ak, pk = stat(keep)
    print(f"ALL BUYS:          n={na:3d} win%={wa:4.0f} avg_pips={aa:+.2f} net$={pa:+.2f}")
    print(f"WOULD-BLOCK (>= {THR:.0f}): n={nb:3d} win%={wb:4.0f} avg_pips={ab:+.2f} net$={pb:+.2f}   <- the veto removes these")
    print(f"KEPT (< {THR:.0f}):        n={nk:3d} win%={wk:4.0f} avg_pips={ak:+.2f} net$={pk:+.2f}   <- what survives the veto")
    print()
    print(f"NET EFFECT OF THE VETO:  ${pa:+.2f} (all)  ->  ${pk:+.2f} (with veto)   delta ${pk - pa:+.2f}")
    frames = [r[3] for r in rec]
    print(f"\n(intraday frames per trade: min={min(frames)} max={max(frames)}  "
          f"-- <4 means 15M/5M not covered, intraday_pos approximated from 1D/1H)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
