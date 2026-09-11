"""Phase B sweep — find the SL/TP geometry with the best return-per-drawdown (READ-ONLY).
Same one-position-at-a-time realism as wrsf_drawdown_model, swept across SL/TP grids. Selector
candidates (good_spot 2D take) are geometry-independent, so precompute once then resolve per grid.
Reports per geometry: n, win%, avg pips, total R, max DD (R, %), longest losing streak, and
RET/DD (Calmar-ish). Never trades.

Run: PYTHONPATH=. .venv/Scripts/python.exe research/mtf_regime_switch/wrsf_geometry_sweep.py
"""
import sys
sys.path.insert(0, ".")
from research.mtf_regime_switch import wrsf_backtest as wb
from research.mtf_regime_switch.good_spot import good_spot_decision

PIP = 0.0001
COST = 0.70
HTF = "2D"
RISK_PCT = 1.1
YEARS = 60000 * 15 / 60 / 24 / 365          # ~ span of 60k M15 bars in years
MAXH_BARS = 120 * 4                          # 120h max hold
SL_GRID = [15, 20, 25]
TP_GRID = [30, 40, 50, 60, 80, 100]


def main():
    bars = wb.load(); n = len(bars)
    highs = [b[1] for b in bars]; lows = [b[2] for b in bars]; closes = [b[3] for b in bars]

    # precompute selector-taken candidates (geometry-independent)
    cands = []   # (i, is_short)
    start = max(wb.SWING_K, max(wb.HTF_WINDOWS.values()))
    for i in range(start, n - 1):
        hi = highs[i]; lo = lows[i]; win_h = max(highs[i-wb.SWING_K:i+1]); win_l = min(lows[i-wb.SWING_K:i+1])
        ih = hi >= win_h; il = lo <= win_l
        if ih == il:
            continue
        fade = "Sell" if ih else "Buy"
        d = good_spot_decision(fade, wb.build_stamp(highs, lows, closes, i), htf_key=HTF,
                               skip_up_buy=True, flip_counter_trend=False)
        if d["action"] == "take":
            cands.append((i, fade == "Sell"))
    print(f"selector-taken candidates: {len(cands)}  (span ~{YEARS:.1f}y)\n")

    def resolve(i, is_short, sl_p, tp_p):
        e = closes[i]
        sl = e + sl_p*PIP if is_short else e - sl_p*PIP
        tp = e - tp_p*PIP if is_short else e + tp_p*PIP
        end = min(i + MAXH_BARS, n - 1)
        for j in range(i + 1, end + 1):
            hit_sl = highs[j] >= sl if is_short else lows[j] <= sl
            hit_tp = lows[j] <= tp if is_short else highs[j] >= tp
            if hit_sl:
                return -sl_p, j
            if hit_tp:
                return tp_p, j
        cl = closes[end]
        return ((e - cl) if is_short else (cl - e)) / PIP, end

    def run(sl_p, tp_p):
        last_exit = -1; pnls = []
        for i, is_short in cands:
            if i <= last_exit:
                continue
            p, ex = resolve(i, is_short, sl_p, tp_p)
            pnls.append((p - COST) / sl_p)      # in R (risk = sl_p)
            last_exit = ex
        if not pnls:
            return None
        nT = len(pnls); wins = sum(1 for r in pnls if r > 0); tot = sum(pnls)
        cum = peak = maxdd = 0.0; streak = maxstk = 0
        for r in pnls:
            cum += r; peak = max(peak, cum); maxdd = max(maxdd, peak - cum)
            streak = streak + 1 if r < 0 else 0; maxstk = max(maxstk, streak)
        return dict(sl=sl_p, tp=tp_p, n=nT, wr=100*wins/nT, avgp=(tot*sl_p)/nT,
                    totR=tot, ddR=maxdd, stk=maxstk,
                    retpct=tot*RISK_PCT/YEARS, ddpct=maxdd*RISK_PCT,
                    calmar=(tot/maxdd if maxdd > 0 else float('inf')))

    print(f"{'SL/TP':8} {'n':>4} {'win%':>5} {'avgp':>6} {'totR':>7} {'DD_R':>6} "
          f"{'ret%/y':>7} {'DD%':>6} {'streak':>6} {'RET/DD':>7}")
    print("-" * 74)
    rows = []
    for sl in SL_GRID:
        for tp in TP_GRID:
            if tp <= sl:
                continue
            r = run(sl, tp)
            if r:
                rows.append(r)
                print(f"{sl}/{tp:<5} {r['n']:>4} {r['wr']:>5.1f} {r['avgp']:>+6.2f} {r['totR']:>+7.1f} "
                      f"{r['ddR']:>6.1f} {r['retpct']:>+6.1f}% {r['ddpct']:>5.1f}% {r['stk']:>6} {r['calmar']:>7.2f}")
    print("-" * 74)
    best = max(rows, key=lambda r: r['calmar'])
    print(f"BEST return/drawdown: SL{best['sl']}/TP{best['tp']}  "
          f"RET/DD={best['calmar']:.2f}  ({best['retpct']:+.1f}%/yr for {best['ddpct']:.0f}% DD, "
          f"win {best['wr']:.0f}%, streak {best['stk']})")
    print("NOTE: in-sample (selector tuned on this 2.4y); DD% at 1.1% risk; forward test still required.")


if __name__ == "__main__":
    main()
