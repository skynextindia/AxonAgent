"""Session-open gap-fill REFINED on M5 (tighten the NY-open reversion mechanics).

M15 found: fade the first ~8p extension from the NY-open (13:00 UTC) back to the
open, ~20p stop = +1.2p, 71% trigger, 81% fill, 7/10 q+. M5 gives precise trigger
detection and honest fill-vs-stop ordering (M15 had to assume stop-first on straddle
bars). Sample is shorter (~16 mo) but execution-accurate.

Sweeps: extension THRESH x STOP; entry-window; target fraction; conservative vs
optimistic intra-bar ordering (should nearly coincide on M5 = the real number).

    python -m research.gap_fill_spectrum.session_open_gapfill_m5
"""
from __future__ import annotations
import csv, os
from datetime import datetime, timezone
from collections import defaultdict
from statistics import mean

PIP = 0.0001
COST = 2.0
TF_MIN = 5
_THIS = os.path.dirname(os.path.abspath(__file__))
M5 = os.path.join(_THIS, "..", "intraday_backtest", "eurusd_m5_mt5.csv")


def load():
    bars = []
    with open(M5) as f:
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


def trade(bars, i, thresh, stop, entry_bars, hold_bars, target_mult, optimistic):
    """Fade the first THRESH extension from the session-open (bar i) back toward the
    open. Target gain = thresh*target_mult pips; stop = `stop` pips beyond entry.
    Returns (net_before_cost, triggered, filled)."""
    pivot = bars[i][1]
    up = pivot + thresh * PIP
    dn = pivot - thresh * PIP
    n = len(bars)
    trig = None
    for j in range(i, min(i + entry_bars, n)):
        _, _, bh, bl, _ = bars[j]
        if bh >= up:
            trig = ("short", up, j); break
        if bl <= dn:
            trig = ("long", dn, j); break
    if trig is None:
        return (0.0, False, False)
    side, entry, j0 = trig
    gain = thresh * target_mult * PIP
    if side == "short":
        tp = entry - gain; sl = entry + stop * PIP
    else:
        tp = entry + gain; sl = entry - stop * PIP
    end = min(i + hold_bars, n)
    for j in range(j0, end):
        _, _, bh, bl, bc = bars[j]
        if side == "short":
            hit_tp = bl <= tp; hit_sl = bh >= sl
        else:
            hit_tp = bh >= tp; hit_sl = bl <= sl
        if hit_tp and hit_sl:
            return (thresh * target_mult if optimistic else -stop, True, optimistic)
        if hit_tp:
            return (thresh * target_mult, True, True)
        if hit_sl:
            return (-stop, True, False)
    lastc = bars[end - 1][4]
    net = (entry - lastc) / PIP if side == "short" else (lastc - entry) / PIP
    return (net, True, False)


def evalcell(bars, opens, thresh, stop, entry_bars, hold_bars, target_mult, optimistic):
    byq = defaultdict(list); trg = fil = tot = 0
    for i in opens:
        tot += 1
        net, triggered, filled = trade(bars, i, thresh, stop, entry_bars, hold_bars, target_mult, optimistic)
        if not triggered:
            continue
        trg += 1; fil += filled
        byq[quarter(bars[i][0])].append(net - COST)
    allv = [p for v in byq.values() for p in v]
    if not allv:
        return None
    per_q = {q: mean(v) for q, v in byq.items()}
    pos = sum(1 for x in per_q.values() if x > 0)
    return dict(n=len(allv), trig=100 * trg / tot, fill=100 * fil / max(1, trg),
                avg=mean(allv), pos=pos, nq=len(per_q))


def find_opens(bars, hour):
    return [i for i, b in enumerate(bars) if b[0].hour == hour and b[0].minute == 0]


def line(label, r):
    if r is None:
        print(f"  {label:22} n=0"); return
    print(f"  {label:22} n={r['n']:4d}  trig {r['trig']:3.0f}%  fill {r['fill']:3.0f}%  "
          f"avg {r['avg']:+6.2f}p  {r['pos']}/{r['nq']} q+")


def main() -> int:
    bars = load()
    ny = find_opens(bars, 13); ldn = find_opens(bars, 7)
    print(f"M5 bars {len(bars)} ({bars[0][0].date()}..{bars[-1][0].date()})")
    print(f"NY opens {len(ny)}  London opens {len(ldn)}  cost={COST}p  tf={TF_MIN}m\n")

    EW = int(90 / TF_MIN); HOLD = int(360 / TF_MIN)   # 90-min trigger window, 6h hold

    print("== NY: extension THRESH x STOP (conservative), target=pivot ==")
    for thresh in (5, 6, 8, 10, 12):
        for stop in (12, 15, 20):
            r = evalcell(bars, ny, thresh, stop, EW, HOLD, 1.0, False)
            line(f"ext{thresh}p / stop{stop}p", r)
        print()

    print("== NY: entry-window sensitivity (ext8 / stop20 / target=pivot) ==")
    for ewm in (30, 45, 60, 90, 120):
        r = evalcell(bars, ny, 8, 20, int(ewm / TF_MIN), HOLD, 1.0, False)
        line(f"entry-window {ewm}min", r)

    print("\n== NY: target fraction (ext8 / stop20), gain = 8*mult pips ==")
    for tm in (0.5, 0.75, 1.0, 1.25, 1.5):
        r = evalcell(bars, ny, 8, 20, EW, HOLD, tm, False)
        line(f"target x{tm:.2f} ({8*tm:.0f}p)", r)

    print("\n== NY: conservative vs optimistic ordering (ext8 / stop20) — M5 should ~coincide ==")
    line("conservative", evalcell(bars, ny, 8, 20, EW, HOLD, 1.0, False))
    line("optimistic  ", evalcell(bars, ny, 8, 20, EW, HOLD, 1.0, True))

    print("\n== LONDON (contrast, ext8/stop20/target=pivot) ==")
    line("London ext8/stop20", evalcell(bars, ldn, 8, 20, EW, HOLD, 1.0, False))

    print("\nREAD: pick the cell with the best avg net AND q+ at a healthy trig%. If")
    print("conservative~=optimistic, that avg is the real execution number, not a bound.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
