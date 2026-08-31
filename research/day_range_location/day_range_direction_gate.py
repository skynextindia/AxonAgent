"""Does DAILY-RANGE POSITION gate fade direction profitably? (user concept 2026-08-31)

USER RULE: never SELL when price is in the lower part of the day's range; only
SELL at the TOP of the day range, only BUY at/below the day-range average.
i.e. tie fade DIRECTION to where price sits inside today's developing range.

Motivation: on 2026-08-31 the machine SOLD at intraday discount (pos ~33, lower
third) on a +41p up-day and lost the stop. The rule would have blocked it.

TEST: replay fade entries on 2y H1 (multi-regime). At each entry, measure the
position inside that UTC day's DEVELOPING range so far, pos = (c-dLo)/(dHi-dLo)
in [0,1]. Then compare net expectancy (bracket sim, net of cost, conservative
stop-first) for:
  * ALL sells / ALL buys                         (baseline fade)
  * sells split upper-half vs lower-half of day  (does location separate P&L?)
  * the USER GATE: SELL only if pos>=hi, BUY only if pos<=lo (drop the rest)
Bucketed by quarter to check regime-robustness. Two brackets: the live scalp
(SL20/TP20) and the wide profile (SL20/TP100).

    python -m research.day_range_location.day_range_direction_gate
"""
from __future__ import annotations
import csv, os
from datetime import datetime, timezone
from collections import defaultdict

PIP = 0.0001
COST_PIPS = 2.0
SWING_K = 4
MAX_HOLD = 120
MIN_DAY_BARS = 3          # need a few bars before the day range is meaningful
MIN_DAY_RANGE_PIPS = 8    # skip days too tight to have a real "position"
_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_THIS, "..", "..")


def load_h1():
    bars = []
    with open(os.path.join(_ROOT, "eurusd_h1_2y.csv")) as f:
        for r in csv.DictReader(f):
            try:
                dt = datetime.fromisoformat((r.get("Datetime") or r["Date"]).replace(" ", "T"))
                dt = dt.astimezone(timezone.utc)
                bars.append((dt, float(r["High"]), float(r["Low"]), float(r["Close"])))
            except Exception:
                continue
    bars.sort(key=lambda x: x[0])
    return bars


def quarter(dt):
    return f"{dt.year}-Q{(dt.month - 1)//3 + 1}"


def build_entries(bars):
    """Return list of dicts: fade entries with day-range position at entry.
    fade = short a local high, long a local low (exhaustion)."""
    # developing day range up to and including each bar (causal)
    day_hi = {}; day_lo = {}; nbars = {}
    dh = dl = None; cur = None; cnt = 0
    dev = []                                    # per-index (day_hi, day_lo, nbars_so_far)
    for i, (dt, hi, lo, c) in enumerate(bars):
        d = dt.date()
        if d != cur:
            cur = d; dh = hi; dl = lo; cnt = 0
        else:
            dh = max(dh, hi); dl = min(dl, lo)
        cnt += 1
        dev.append((dh, dl, cnt))

    entries = []
    n = len(bars)
    for i in range(SWING_K, n - 1):
        dt, hi, lo, c = bars[i]
        win = bars[i - SWING_K:i + 1]
        is_high = hi >= max(b[1] for b in win)
        is_low = lo <= min(b[2] for b in win)
        if is_high == is_low:
            continue
        dh, dl, cnt = dev[i]
        rng = dh - dl
        if cnt < MIN_DAY_BARS or rng < MIN_DAY_RANGE_PIPS * PIP:
            continue
        pos = (c - dl) / rng                    # 0 = day low, 1 = day high
        entries.append({
            "i": i, "q": quarter(dt), "short": bool(is_high),  # fade: short a high
            "pos": pos, "entry": c,
        })
    return entries


def sim_one(bars, e, sl_p, tp_p):
    """Bar-by-bar bracket outcome for one entry (conservative stop-first). net pips."""
    i = e["i"]; short = e["short"]; entry = e["entry"]
    sl = sl_p * PIP; tp = tp_p * PIP
    n = len(bars); outcome = None
    for j in range(i + 1, min(i + 1 + MAX_HOLD, n)):
        _, bh, bl, bc = bars[j]
        if short:
            hit_sl = bh >= entry + sl; hit_tp = bl <= entry - tp
        else:
            hit_sl = bl <= entry - sl; hit_tp = bh >= entry + tp
        if hit_sl and hit_tp:
            outcome = -sl_p; break
        if hit_tp:
            outcome = tp_p; break
        if hit_sl:
            outcome = -sl_p; break
    if outcome is None:
        lastc = bars[min(i + MAX_HOLD, n - 1)][3]
        outcome = ((entry - lastc) if short else (lastc - entry)) / PIP
    return outcome - COST_PIPS


def stats(vals):
    n = len(vals)
    if n == 0:
        return (0, 0.0, 0.0)
    s = sum(vals)
    return (n, s / n, s)


def per_quarter_positive(rows_by_q):
    return sum(1 for q, v in rows_by_q.items() if stats(v)[1] > 0), len(rows_by_q)


def report_bracket(bars, entries, sl_p, tp_p):
    print(f"\n================ BRACKET SL{sl_p}/TP{tp_p} (1:{tp_p/sl_p:.1f})  cost {COST_PIPS}p ================")
    # attach net pips
    for e in entries:
        e["net"] = sim_one(bars, e, sl_p, tp_p)

    sells = [e for e in entries if e["short"]]
    buys = [e for e in entries if not e["short"]]

    def block(name, subset):
        by_q = defaultdict(list)
        for e in subset:
            by_q[e["q"]].append(e["net"])
        n, avg, tot = stats([e["net"] for e in subset])
        pos, nq = per_quarter_positive(by_q)
        print(f"  {name:38} n={n:5d}  avg {avg:+6.2f}p  total {tot:+9.1f}p  {pos}/{nq} qtrs+")
        return avg

    print("  -- baseline --")
    block("ALL fades", entries)
    block("ALL sells (fade highs)", sells)
    block("ALL buys  (fade lows)", buys)

    print("  -- location split (does day-range pos separate P&L?) --")
    block("sells UPPER half (pos>=0.5)", [e for e in sells if e["pos"] >= 0.5])
    block("sells LOWER half (pos<0.5)", [e for e in sells if e["pos"] < 0.5])
    block("buys  LOWER half (pos<0.5)", [e for e in buys if e["pos"] < 0.5])
    block("buys  UPPER half (pos>=0.5)", [e for e in buys if e["pos"] >= 0.5])

    print("  -- USER GATE: SELL only pos>=hi, BUY only pos<=lo (drop rest) --")
    for hi, lo in ((0.5, 0.5), (0.6, 0.4), (0.67, 0.33), (0.75, 0.25)):
        gated = [e for e in sells if e["pos"] >= hi] + [e for e in buys if e["pos"] <= lo]
        by_q = defaultdict(list)
        for e in gated:
            by_q[e["q"]].append(e["net"])
        n, avg, tot = stats([e["net"] for e in gated])
        pos, nq = per_quarter_positive(by_q)
        keep = n / max(1, len(entries))
        print(f"    hi={hi:.2f} lo={lo:.2f}: n={n:5d} ({keep:4.0%} kept)  avg {avg:+6.2f}p  "
              f"total {tot:+9.1f}p  {pos}/{nq} qtrs+")

    print("  -- INVERSE GATE (what the split implies): SELL only pos<=lo, BUY only pos>=hi --")
    for lo, hi in ((0.5, 0.5), (0.4, 0.6), (0.33, 0.67), (0.25, 0.75)):
        gated = [e for e in sells if e["pos"] <= lo] + [e for e in buys if e["pos"] >= hi]
        by_q = defaultdict(list)
        for e in gated:
            by_q[e["q"]].append(e["net"])
        n, avg, tot = stats([e["net"] for e in gated])
        pos, nq = per_quarter_positive(by_q)
        keep = n / max(1, len(entries))
        print(f"    lo={lo:.2f} hi={hi:.2f}: n={n:5d} ({keep:4.0%} kept)  avg {avg:+6.2f}p  "
              f"total {tot:+9.1f}p  {pos}/{nq} qtrs+")


def main() -> int:
    bars = load_h1()
    entries = build_entries(bars)
    qs = sorted({e["q"] for e in entries})
    print(f"H1 bars {len(bars)}  ({bars[0][0].date()}..{bars[-1][0].date()})")
    print(f"fade entries with a valid day-range position: {len(entries)}  across {len(qs)} quarters")
    print(f"(min {MIN_DAY_BARS} bars into day, min {MIN_DAY_RANGE_PIPS}p day range, swing_k={SWING_K})")

    # sanity: distribution of sells/buys by half
    sells = [e for e in entries if e["short"]]
    buys = [e for e in entries if not e["short"]]
    su = sum(1 for e in sells if e["pos"] >= 0.5); bl = sum(1 for e in buys if e["pos"] < 0.5)
    print(f"sells: {len(sells)} ({su} upper / {len(sells)-su} lower)   "
          f"buys: {len(buys)} ({bl} lower / {len(buys)-bl} upper)")

    report_bracket(bars, entries, 20, 20)     # the live scalp
    report_bracket(bars, entries, 20, 100)    # the wide profile

    print("\nNOTE: 'short a high' already fades an extreme; this gate adds the DAY-RANGE")
    print("location on top. If the USER GATE rows don't beat 'ALL fades' at similar")
    print("qtrs+, the daily-range direction filter is not an edge (matches [[daily-stretch-per-pair]]).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
