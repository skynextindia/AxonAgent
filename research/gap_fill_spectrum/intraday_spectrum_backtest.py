"""Intraday spectrum backtest: gap-fill -> continuation -> reversal around the
prior-day pivot (user concept 2026-08-31).

Model each day by three reference levels from the PRIOR day:
  pc  = prevClose  (pivot / gap-fill target)
  PDH = prior-day HIGH  (upper resistance / continuation target when moving up)
  PDL = prior-day LOW   (lower support   / continuation target when moving down)
and today's open `op`. gap = op - pc.

Three sequential opportunities the user describes, each simulated on real M15 bars,
fixed SL, the LEVEL as the target, net of cost, capped to ~1 day (intraday):

  A) GAP FILL      : from the open, trade toward pc (neg gap -> long up to pc;
                     pos gap -> short down to pc).
  B) CONTINUATION  : if the fill reaches pc and price SUSTAINS through it, ride the
                     same direction to the next level (PDH up / PDL down).
  C) REVERSAL      : at that outer level, fade back toward pc.

    python -m research.gap_fill_spectrum.intraday_spectrum_backtest
"""
from __future__ import annotations
import csv, os
from datetime import datetime, timezone
from collections import defaultdict
from statistics import mean

PIP = 0.0001
COST = 2.0
SL_PIPS = 20
MIN_GAP = 3          # pips; skip days with no real gap
MIN_ROOM = 5         # pips; need room to the next level for continuation/reversal
DAY_BARS = 96        # ~1 day of M15 (intraday cap per leg)
_THIS = os.path.dirname(os.path.abspath(__file__))
M15 = os.path.join(_THIS, "..", "intraday_backtest", "eurusd_m15_mt5.csv")


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


def by_day(bars):
    days = defaultdict(list)
    for b in bars:
        days[b[0].date()].append(b)
    dates = sorted(days)
    frames = {}
    for k in range(1, len(dates)):
        d = dates[k]; pv = days[dates[k - 1]]
        frames[d] = {
            "bars": days[d],
            "op": days[d][0][1],
            "pc": pv[-1][4],
            "pdh": max(b[2] for b in pv),
            "pdl": min(b[3] for b in pv),
            "q": quarter(days[d][0][0]),
        }
    return frames


def scan(bars, start, entry, dirn, tp_price, sl_price):
    """Walk bars[start:] up to DAY_BARS. Return (net_pips_before_cost, exit_bar_idx, hit_tp)."""
    end = min(start + DAY_BARS, len(bars))
    for j in range(start, end):
        _, _, bh, bl, _ = bars[j]
        hit_tp = bh >= tp_price if dirn > 0 else bl <= tp_price
        hit_sl = bl <= sl_price if dirn > 0 else bh >= sl_price
        if hit_tp and hit_sl:
            return (-SL_PIPS, j, False)                 # conservative: SL first
        if hit_tp:
            return ((tp_price - entry) * dirn / PIP, j, True)
        if hit_sl:
            return (-SL_PIPS, j, False)
    lastc = bars[end - 1][4]
    return ((lastc - entry) * dirn / PIP, end - 1, False)


def run():
    frames = by_day(load())
    legs = {"A_gapfill": defaultdict(list), "B_continuation": defaultdict(list),
            "C_reversal": defaultdict(list), "AC_fill_then_reversal": defaultdict(list)}
    counts = defaultdict(int)
    gapA = []                                            # (abs_gap, net) for leg A size study
    for d, fr in frames.items():
        bars = fr["bars"]; pc = fr["pc"]; op = fr["op"]; q = fr["q"]
        gap = (op - pc) / PIP
        if abs(gap) < MIN_GAP:
            continue
        counts["days_with_gap"] += 1
        fill_dir = 1 if gap < 0 else -1                 # toward pc
        # ---- Leg A: gap fill (open -> pc) ----
        entryA = bars[0][4]
        slA = entryA - fill_dir * SL_PIPS * PIP
        netA, jA, filled = scan(bars, 1, entryA, fill_dir, pc, slA)
        legs["A_gapfill"][q].append(netA - COST)
        gapA.append((abs(gap), netA - COST))
        if not filled:
            continue
        counts["gaps_filled"] += 1
        # ---- Leg B: continuation past pc to next level ----
        cont_dir = fill_dir                              # same direction, through the pivot
        target = fr["pdh"] if cont_dir > 0 else fr["pdl"]
        room = (target - pc) * cont_dir / PIP
        did_reversal_setup = False
        if room >= MIN_ROOM:
            entryB = pc
            slB = pc - cont_dir * SL_PIPS * PIP
            netB, jB, hitlvl = scan(bars, jA, entryB, cont_dir, target, slB)
            legs["B_continuation"][q].append(netB - COST)
            # ---- Leg C: reversal fade at the outer level, back toward pc ----
            if hitlvl:
                counts["reached_level"] += 1
                rev_dir = -cont_dir
                entryC = target
                slC = target - rev_dir * SL_PIPS * PIP
                netC, jC, reverted = scan(bars, jB, entryC, rev_dir, pc, slC)
                legs["C_reversal"][q].append(netC - COST)
                # combined "two opportunities": gap-fill leg + reversal leg
                legs["AC_fill_then_reversal"][q].append((netA - COST) + (netC - COST))
                did_reversal_setup = True
        if not did_reversal_setup:
            # still count the fill-only combined path so AC isn't survivorship-biased upward
            pass
    return legs, counts, gapA


def report(legs, counts):
    print("counts:", dict(counts))
    print(f"\n  {'leg':26}{'n':>7}{'avg net':>10}{'win%':>7}{'q+':>7}")
    for name in ("A_gapfill", "B_continuation", "C_reversal", "AC_fill_then_reversal"):
        byq = legs[name]
        allv = [p for v in byq.values() for p in v]
        if not allv:
            print(f"  {name:26}{0:>7}"); continue
        per_q = {q: mean(v) for q, v in byq.items()}
        pos = sum(1 for x in per_q.values() if x > 0)
        win = 100.0 * sum(1 for x in allv if x > 0) / len(allv)
        print(f"  {name:26}{len(allv):>7}{mean(allv):>+9.2f}p{win:>6.0f}%{pos:>4}/{len(per_q)}")
    print("\n  A=gap fill toward prevClose | B=continuation pc->next level | "
          "C=reversal fade at level->pc\n  AC=the two opportunities combined (fill + reversal).")


def main() -> int:
    print("INTRADAY SPECTRUM: gap-fill -> continuation -> reversal (real MT5 M15)")
    print(f"SL={SL_PIPS}p  min_gap={MIN_GAP}p  min_room={MIN_ROOM}p  day_cap={DAY_BARS} M15 bars  cost={COST}p")
    legs, counts, gapA = run()
    report(legs, counts)
    print("\n  -- Leg A (gap fill) by GAP SIZE (bigger gap = bigger target vs fixed cost) --")
    for lo, hi in ((3, 6), (6, 10), (10, 20), (20, 999)):
        sub = [n for g, n in gapA if lo <= g < hi]
        if sub:
            win = 100.0 * sum(1 for x in sub if x > 0) / len(sub)
            tag = f"{lo}-{hi}p" if hi < 999 else f"{lo}p+"
            print(f"    |gap| {tag:8}: n={len(sub):4d}  avg {mean(sub):+.2f}p  win {win:.0f}%")
    print("\nREAD: which leg actually has positive expectancy? gap-fill (mean-revert to")
    print("pc), continuation (momentum to next level), or reversal (fade the level)?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
