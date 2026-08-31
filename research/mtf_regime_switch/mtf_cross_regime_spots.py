"""MTF cross-regime detection: find the ACTUAL good entry spots (user 2026-09-01).

Instead of always-fade or a single ER switch, reconstruct a multi-timeframe trend
stack for every entry and measure the expectancy of the RIGHT-DIRECTION trade in each
cross-regime configuration. Rank the buckets to find where entries are genuinely good.

TFs reconstructed from M15 closes (Kaufman efficiency ratio + net direction):
  LTF  ~4h  (16 bars)   MTF ~16h (64)   HTF ~2d (192)   XTF ~4d (384)
Each is UP / DOWN / RANGE. The entry itself is a local swing high/low (SWING_K).

For each cross-state we test the trade you'd actually take there:
  - aligned trend + pullback swing against it -> trade WITH the trend (buy dip/sell rally)
  - all range -> fade the swing extreme
and report net expectancy (SL20/TP100, 5-day hold, limit cost 1.2p), n, quarters+.

    python -m research.mtf_regime_switch.mtf_cross_regime_spots
"""
from __future__ import annotations
import csv, os
from datetime import datetime, timezone
from collections import defaultdict
from statistics import mean

PIP = 0.0001; SWING_K = 6; COST = 1.2; MAXHOLD = 480; SL_P = 20; TP_P = 100
ERM = 0.50                         # |net/range| >= ERM => trend, else RANGE (per-TF)
WINS = {"LTF": 16, "MTF": 64, "HTF": 192, "XTF": 384}
_THIS = os.path.dirname(os.path.abspath(__file__))
M15 = os.path.join(_THIS, "..", "intraday_backtest", "eurusd_m15_mt5.csv")


def load():
    b = []
    with open(M15) as f:
        for r in csv.DictReader(f):
            dt = datetime.fromisoformat(r["Datetime"].replace(" ", "T"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            b.append((dt.astimezone(timezone.utc), float(r["High"]), float(r["Low"]), float(r["Close"])))
    b.sort(key=lambda x: x[0]); return b


def q(dt): return f"{dt.year}-Q{(dt.month - 1)//3 + 1}"


def main() -> int:
    bars = load(); n = len(bars); closes = [b[3] for b in bars]

    highs = [b[1] for b in bars]; lows = [b[2] for b in bars]

    def tf(i, w):
        """Trend via net-move / window-range (bounded [-1,1], stable across window
        lengths, unlike ER which shrinks). |ratio|>=ERM => directional, else RANGE."""
        if i < w:
            return 0
        net = closes[i] - closes[i - w]
        rng = max(highs[i - w:i + 1]) - min(lows[i - w:i + 1])
        if rng <= 0:
            return 0
        r = net / rng
        return 0 if abs(r) < ERM else (1 if r > 0 else -1)

    def out(i, entry, short):
        sl = SL_P * PIP; tp = TP_P * PIP
        for j in range(i + 1, min(i + 1 + MAXHOLD, n)):
            _, bh, bl, bc = bars[j]
            if short:
                hs = bh >= entry + sl; ht = bl <= entry - tp
            else:
                hs = bl <= entry - sl; ht = bh >= entry + tp
            if hs and ht: return -SL_P
            if ht: return TP_P
            if hs: return -SL_P
        return ((entry - bars[min(i + MAXHOLD, n - 1)][3]) if short
                else (bars[min(i + MAXHOLD, n - 1)][3] - entry)) / PIP

    # gather entries with full MTF stack + both-direction outcomes
    E = []
    for i in range(max(SWING_K, WINS["XTF"]), n - 1):
        dt, hi, lo, c = bars[i]; win = bars[i - SWING_K:i + 1]
        ih = hi >= max(b[1] for b in win); il = lo <= min(b[2] for b in win)
        if ih == il:
            continue
        st = {k: tf(i, w) for k, w in WINS.items()}
        E.append({"i": i, "c": c, "ih": ih, "q": q(dt), "st": st,
                  "net_long": out(i, c, False) - COST, "net_short": out(i, c, True) - COST})
    print(f"entries {len(E)}  ERM={ERM}  bracket SL{SL_P}/TP{TP_P} 5d hold, limit cost {COST}p\n")

    def agg(sub, pick):
        """pick(e)->'L'/'S'/None. Return n, avg, q+."""
        byq = defaultdict(list)
        for e in sub:
            s = pick(e)
            if s is None: continue
            byq[e["q"]].append(e["net_long"] if s == "L" else e["net_short"])
        allv = [p for v in byq.values() for p in v]
        if not allv: return (0, 0.0, 0, 0)
        perq = {k: mean(v) for k, v in byq.items()}
        return (len(allv), mean(allv), sum(1 for x in perq.values() if x > 0), len(perq))

    def show(label, sub, pick):
        nn, a, pos, nq = agg(sub, pick)
        if nn == 0: print(f"  {label:46} n=0"); return (a, nn)
        print(f"  {label:46} n={nn:5d}  avg {a:+5.2f}p  {pos}/{nq}q+")
        return (a, nn)

    HTF = lambda e: e["st"]["HTF"]; MTF = lambda e: e["st"]["MTF"]
    withtrend = lambda e: ("L" if HTF(e) > 0 else "S") if HTF(e) != 0 else None
    fade = lambda e: "S" if e["ih"] else "L"

    print("== 1. Direction by HTF regime (which way to trade in each HTF state) ==")
    show("HTF UP  -> go LONG (with)", [e for e in E if HTF(e) > 0], lambda e: "L")
    show("HTF UP  -> FADE (short the high / long low)", [e for e in E if HTF(e) > 0], fade)
    show("HTF DOWN-> go SHORT (with)", [e for e in E if HTF(e) < 0], lambda e: "S")
    show("HTF DOWN-> FADE", [e for e in E if HTF(e) < 0], fade)
    show("HTF RANGE-> FADE", [e for e in E if HTF(e) == 0], fade)
    show("HTF RANGE-> momentum (with swing)", [e for e in E if HTF(e) == 0],
         lambda e: "L" if e["ih"] else "S")

    print("\n== 2. PULLBACK vs CONTINUATION (the classic 'good spot') ==")
    show("HTF UP + swing LOW  -> BUY dip (pullback)", [e for e in E if HTF(e) > 0 and not e["ih"]], lambda e: "L")
    show("HTF UP + swing HIGH -> BUY breakout (cont)", [e for e in E if HTF(e) > 0 and e["ih"]], lambda e: "L")
    show("HTF DN + swing HIGH -> SELL rally (pullback)", [e for e in E if HTF(e) < 0 and e["ih"]], lambda e: "S")
    show("HTF DN + swing LOW  -> SELL breakdown (cont)", [e for e in E if HTF(e) < 0 and not e["ih"]], lambda e: "S")

    print("\n== 3. MULTI-TF ALIGNMENT depth (HTF & MTF agree?) with-trend ==")
    show("HTF&MTF both UP   -> LONG", [e for e in E if HTF(e) > 0 and MTF(e) > 0], lambda e: "L")
    show("HTF&MTF both DOWN -> SHORT", [e for e in E if HTF(e) < 0 and MTF(e) < 0], lambda e: "S")
    show("HTF up, MTF not-up (conflict) -> LONG", [e for e in E if HTF(e) > 0 and MTF(e) <= 0], lambda e: "L")
    show("all 4 TFs UP -> LONG", [e for e in E if all(v > 0 for v in e["st"].values())], lambda e: "L")
    show("all 4 TFs DOWN -> SHORT", [e for e in E if all(v < 0 for v in e["st"].values())], lambda e: "S")

    print("\n== 4. BEST COMBINED STRATEGY: with-trend on aligned pullback, fade in range ==")
    def adaptive(e):
        h = HTF(e)
        if h == 0:
            return "S" if e["ih"] else "L"          # range -> fade
        if h > 0:
            return "L"                               # uptrend -> long (any swing)
        return "S"                                   # downtrend -> short
    show("ADAPTIVE (with-HTF trend / fade in range)", E, adaptive)
    def adaptive_pull(e):
        h = HTF(e)
        if h == 0:
            return "S" if e["ih"] else "L"
        if h > 0 and not e["ih"]:
            return "L"                               # only buy DIPS in uptrend
        if h < 0 and e["ih"]:
            return "S"                               # only sell RALLIES in downtrend
        return None                                  # skip continuation/counter
    show("ADAPTIVE-PULLBACK (dips up / rallies down / fade range)", E, adaptive_pull)

    def goodspots(e):
        h = HTF(e)
        if h < 0 and e["ih"]:
            return "S"                               # sell rallies in downtrend
        if h == 0:
            return "S" if e["ih"] else "L"           # fade in range
        return None                                  # SKIP uptrends + downtrend-lows
    show("GOOD-SPOTS ONLY (sell down-rallies + fade range, skip up)", E, goodspots)

    def goodspots_aligned(e):
        h = HTF(e); m = MTF(e)
        if h < 0 and m < 0 and e["ih"]:
            return "S"                               # sell rally, HTF&MTF both down
        if h == 0:
            return "S" if e["ih"] else "L"           # fade in range
        return None
    show("GOOD-SPOTS + MTF-align (rally-sell needs MTF down too)", E, goodspots_aligned)

    print("\nREAD: rank the buckets by avg AND q+. 'Good spot' = the cross-state where the")
    print("with-trend (or fade) trade has the highest, most regime-robust expectancy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
