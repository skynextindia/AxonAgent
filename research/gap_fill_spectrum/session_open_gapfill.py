"""Session-open gap-fill: does price revert to the SESSION-OPEN price after an
opening extension? (larger, more realistic sample than the 00:00-UTC day gap.)

FX is continuous, so the tradeable "gap" at a session open is the OPENING DRIVE:
price pushes away from the session-open price in the first ~90 min, then often
reverts back to it. This is the intraday analog of gap-fill-to-prevClose.

Model (causal, on real MT5 M15), for London (07:00 UTC) and NY (13:00 UTC) opens:
  pivot = session-open price.
  Watch the ENTRY window. The FIRST time price extends THRESH pips from pivot, enter
  a FADE back toward pivot (extended up -> SELL; down -> BUY). Target = pivot
  (fill THRESH pips); Stop = STOP pips beyond the entry. Resolve by session end.
Swept over THRESH and STOP; bucketed by quarter and session. Net of cost.

    python -m research.gap_fill_spectrum.session_open_gapfill
"""
from __future__ import annotations
import csv, os
from datetime import datetime, timezone
from collections import defaultdict
from statistics import mean

PIP = 0.0001
COST = 2.0
ENTRY_BARS = 6        # 90 min window to trigger (M15)
SESSION_BARS = 24     # 6h session to fill/stop (M15)
_THIS = os.path.dirname(os.path.abspath(__file__))
M15 = os.path.join(_THIS, "..", "intraday_backtest", "eurusd_m15_mt5.csv")
SESSIONS = [("London", 7, 0), ("NY", 13, 0)]


def load():
    bars = []
    with open(M15) as f:
        for r in csv.DictReader(f):
            dt = datetime.fromisoformat(r["Datetime"].replace(" ", "T"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            bars.append((dt.astimezone(timezone.utc), float(r["Open"]), float(r["High"]),
                         float(r["Low"]), float(r["Close"])))
    bars.sort(key=lambda x: x[0])
    return bars


def quarter(dt):
    return f"{dt.year}-Q{(dt.month - 1)//3 + 1}"


def trade(bars, i, thresh, stop):
    """From session-open bar i: trigger a fade at THRESH extension, target pivot.
    Return (net_pips_before_cost, triggered_bool, filled_bool) or None."""
    pivot = bars[i][1]                     # session-open price (bar open)
    up = pivot + thresh * PIP
    dn = pivot - thresh * PIP
    n = len(bars)
    trig = None
    for j in range(i, min(i + ENTRY_BARS, n)):
        _, _, bh, bl, _ = bars[j]
        if bh >= up:
            trig = ("short", up, j); break
        if bl <= dn:
            trig = ("long", dn, j); break
    if trig is None:
        return None
    side, entry, j0 = trig
    if side == "short":
        tp = pivot; sl = entry + stop * PIP
    else:
        tp = pivot; sl = entry - stop * PIP
    end = min(i + SESSION_BARS, n)
    for j in range(j0, end):
        _, _, bh, bl, bc = bars[j]
        if side == "short":
            hit_tp = bl <= tp; hit_sl = bh >= sl
        else:
            hit_tp = bh >= tp; hit_sl = bl <= sl
        if hit_tp and hit_sl:
            return (-stop, True, False)          # conservative
        if hit_tp:
            return (thresh, True, True)          # filled back to pivot (gain THRESH)
        if hit_sl:
            return (-stop, True, False)
    lastc = bars[end - 1][4]
    net = (entry - lastc) / PIP if side == "short" else (lastc - entry) / PIP
    return (net, True, False)


def main() -> int:
    bars = load()
    # index the session-open bars
    opens = defaultdict(list)      # session name -> list of bar indices
    for i, b in enumerate(bars):
        dt = b[0]
        for name, h, m in SESSIONS:
            if dt.hour == h and dt.minute == m:
                opens[name].append(i)
    print(f"M15 bars {len(bars)} ({bars[0][0].date()}..{bars[-1][0].date()})")
    print("session opens found: " + ", ".join(f"{k}={len(v)}" for k, v in opens.items()))
    print(f"entry window={ENTRY_BARS*15}min  session={SESSION_BARS*15//60}h  cost={COST}p\n")

    for stop in (15, 20):
        print(f"================ STOP {stop}p beyond entry ================")
        print(f"  {'session/thresh':20}{'n':>6}{'trig%':>7}{'fill%':>7}{'avg net':>10}{'q+':>7}")
        for name, _, _ in SESSIONS:
            for thresh in (8, 12, 16, 20):
                byq = defaultdict(list)
                trg = 0; fil = 0; tot = 0
                for i in opens[name]:
                    tot += 1
                    r = trade(bars, i, thresh, stop)
                    if r is None:
                        continue
                    net, triggered, filled = r
                    trg += triggered; fil += filled
                    byq[quarter(bars[i][0])].append(net - COST)
                allv = [p for v in byq.values() for p in v]
                if not allv:
                    continue
                per_q = {q: mean(v) for q, v in byq.items()}
                pos = sum(1 for x in per_q.values() if x > 0)
                print(f"  {name+' ext'+str(thresh)+'p':20}{len(allv):>6}"
                      f"{100*trg/tot:>6.0f}%{100*fil/max(1,trg):>6.0f}%"
                      f"{mean(allv):>+9.2f}p{pos:>4}/{len(per_q)}")
        print()

    print("READ: trig% = how often price extended THRESH from the open (sample size);")
    print("fill% = of those, how often it reverted to the open. Positive avg net at a")
    print("healthy trig% = a real session-open gap-fill edge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
