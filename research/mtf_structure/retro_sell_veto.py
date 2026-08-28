"""Retro-test: did the MTF 'SELL-fade risky' read separate losing sells from winners?

Read-only. For every historical SELL trade in reports/signals.jsonl we reconstruct
the MTF structure AS OF the entry moment (entry_time = close - hold_seconds) using
only daily+hourly bars that had closed BEFORE entry (no lookahead), then evaluate
several candidate direction-veto flags and compare win-rate / avg-pips for
flag=True vs flag=False. The exact module flag (fade_read 'SELL-fade risky') needs
only 5Y/1Y/3M/1M/1W (daily) + 1H (hourly), so the 60-day 15m/5m gap is irrelevant.

    python -m research.mtf_structure.retro_sell_veto
"""
from __future__ import annotations
import csv, json, os
from datetime import datetime, timedelta, timezone
from .structure import classify_tf, MTFSnapshot

PIP = 0.0001
_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_THIS, "..", "..")
SIGNALS = os.path.join(_ROOT, "reports", "signals.jsonl")


def _load_csv(fn, is_hourly):
    rows = []
    with open(os.path.join(_ROOT, fn)) as f:
        for r in csv.DictReader(f):
            t = r.get("Datetime") or r.get("Date")
            try:
                H = float(r["High"]); L = float(r["Low"]); C = float(r["Close"])
            except Exception:
                continue
            try:
                if is_hourly:
                    dt = datetime.fromisoformat(t.replace(" ", "T"))
                    dt = dt.astimezone(timezone.utc)
                else:
                    dt = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
            except Exception:
                continue
            rows.append((dt, H, L, C))
    rows.sort(key=lambda x: x[0])
    return rows


def _slice_before(rows, cutoff):
    """H,L,C lists for all bars strictly before cutoff (no lookahead)."""
    H = []; L = []; C = []
    for dt, h, l, c in rows:
        if dt < cutoff:
            H.append(h); L.append(l); C.append(c)
        else:
            break
    return H, L, C


def _mtf_as_of(daily, hourly, entry_time, cur):
    dcut = entry_time.replace(hour=0, minute=0, second=0, microsecond=0)  # strictly before entry DAY
    dH, dL, dC = _slice_before(daily, dcut)
    hH, hL, hC = _slice_before(hourly, entry_time)
    plan = [("5Y", dH, dL, dC, 1260), ("1Y", dH, dL, dC, 252), ("3M", dH, dL, dC, 63),
            ("1M", dH, dL, dC, 21), ("1W", dH, dL, dC, 5), ("1H", hH, hL, hC, 12)]
    tfs = []
    for name, H, L, C, bars in plan:
        tf = classify_tf(name, H, L, C, cur, PIP, bars)
        if tf is not None:
            tfs.append(tf)
    if len(tfs) < 4:
        return None
    return MTFSnapshot(price=round(cur, 5), tfs=tfs)


def main() -> int:
    daily = _load_csv("eurusd_daily_10y.csv", is_hourly=False)
    hourly = _load_csv("eurusd_h1_2y.csv", is_hourly=True)
    print(f"daily bars {len(daily)} ({daily[0][0].date()}..{daily[-1][0].date()})  "
          f"hourly bars {len(hourly)} ({hourly[0][0].date()}..{hourly[-1][0].date()})")

    sells = []
    with open(SIGNALS) as f:
        for line in f:
            line = line.strip()
            if '"trade_closed"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("direction") != "SELL":
                continue
            sells.append(d)
    print(f"SELL trades in journal: {len(sells)}")

    # candidate flags, each a function(snapshot)->bool
    def f_module(s):      # exact fade_read flag
        return s.fade_read()["note"].startswith("SELL-fade risky")
    def f_fastmom_up(s):  # micro momentum still UP (selling into rising micro)
        return s.fade_read()["short_tf_momentum"] == "UP"
    def f_1h_up(s):       # selling into a rising hour
        t = s.get("1H"); return t is not None and t.trend == "UP"
    def f_macro_prem(s):  # macro premium (>=60)
        return s.premium_discount()["macro_pos"] >= 60.0
    flags = [("module SELL-fade-risky", f_module), ("fast-mom UP", f_fastmom_up),
             ("1H trend UP", f_1h_up), ("macro premium>=60", f_macro_prem)]

    rec = []  # (pips, win, {flagname:bool})
    skipped = 0
    for d in sells:
        try:
            close_t = datetime.fromisoformat(d["timestamp_utc"].replace("Z", "+00:00"))
            entry_t = close_t - timedelta(seconds=float(d.get("hold_seconds", 0) or 0))
            cur = float(d["entry_price"]); pips = float(d.get("pips", 0))
        except Exception:
            skipped += 1; continue
        snap = _mtf_as_of(daily, hourly, entry_t, cur)
        if snap is None:
            skipped += 1; continue
        fv = {name: bool(fn(snap)) for name, fn in flags}
        rec.append((pips, pips > 0, fv))
    print(f"reconstructed {len(rec)} sells  (skipped {skipped})\n")

    def stats(subset):
        n = len(subset)
        if n == 0: return (0, 0.0, 0.0)
        wins = sum(1 for p, w, _ in subset if w)
        avg = sum(p for p, _, _ in subset) / n
        return (n, wins / n * 100, avg)

    n_all, wr_all, avg_all = stats(rec)
    print(f"ALL SELLS: n={n_all}  win%={wr_all:.0f}  avg_pips={avg_all:+.2f}\n")
    print(f"{'flag':26s} {'grp':4s} {'n':>4s} {'win%':>6s} {'avgP':>8s}   separation")
    for name, _ in flags:
        on = [r for r in rec if r[2][name]]
        off = [r for r in rec if not r[2][name]]
        n1, w1, a1 = stats(on); n0, w0, a0 = stats(off)
        sep = a0 - a1  # how much WORSE the flagged group is (positive = flag marks losers)
        print(f"{name:26s} {'ON':4s} {n1:4d} {w1:6.0f} {a1:+8.2f}")
        print(f"{'':26s} {'off':4s} {n0:4d} {w0:6.0f} {a0:+8.2f}   dPips(off-on)={sep:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
