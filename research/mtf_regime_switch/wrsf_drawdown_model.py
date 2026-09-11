"""Phase B — WRSF drawdown / risk model (READ-ONLY analysis). Never trades.

Replays the 2.4y WRSF-SELECTED trade sequence (good_spot 2D, skip_up_buy ON, flip OFF; fade leg
at SL20/TP100, market entry, real 0.70p cost) in chronological order and measures the reality of
a ~17%-win / 1:5 engine:
  * longest losing STREAK and the worst peak-to-trough DRAWDOWN (in R, %, $)
  * distribution of losses BETWEEN wins (how long you wait for a +100p)
  * $300 daily-loss cap interaction (does it help or clip recoveries?)

Sizing: 1R = RISK_PCT% of ACCOUNT risked off the 20p SL. Loss -20p = -1R; win +100p = +5R.
Fixed-fractional off a constant base (conservative; DD in R/pips is base-invariant).

Run: PYTHONPATH=. .venv/Scripts/python.exe research/mtf_regime_switch/wrsf_drawdown_model.py
"""
import sys
from collections import defaultdict
sys.path.insert(0, ".")
from research.mtf_regime_switch import wrsf_backtest as wb
from research.mtf_regime_switch.good_spot import good_spot_decision

ACCOUNT = 9000.0        # lead Eightcap ~$9k
RISK_PCT = 1.1
COST = 0.70
HTF = "2D"
DAILY_CAP = 300.0       # live daily-loss cap ($)
USD_PER_PIP = RISK_PCT / 100.0 * ACCOUNT / wb.SL_P    # $/pip at 1.1% off the 20p SL


PIP = 0.0001
MAXBARS = int(wb.TP_P and 120 * 4)   # 120h max hold = 480 M15 bars


def build_trades():
    """One-position-at-a-time (realistic): after entering, skip new entries until the current
    position resolves (SL20 / TP100 / 120h max-hold). Returns [(dt, net_pips, exit_i)]."""
    bars = wb.load(); n = len(bars)
    highs = [b[1] for b in bars]; lows = [b[2] for b in bars]; closes = [b[3] for b in bars]

    def resolve(i, is_short):
        e = closes[i]
        sl = e + wb.SL_P*PIP if is_short else e - wb.SL_P*PIP
        tp = e - wb.TP_P*PIP if is_short else e + wb.TP_P*PIP
        end = min(i + MAXBARS, n - 1)
        for j in range(i + 1, end + 1):
            hit_sl = highs[j] >= sl if is_short else lows[j] <= sl
            hit_tp = lows[j] <= tp if is_short else highs[j] >= tp
            if hit_sl:                        # adverse-first when both in a bar
                return -wb.SL_P, j
            if hit_tp:
                return wb.TP_P, j
        cl = closes[end]
        return ((e - cl) if is_short else (cl - e)) / PIP, end

    trades = []; last_exit = -1
    start = max(wb.SWING_K, max(wb.HTF_WINDOWS.values()))
    for i in range(start, n - 1):
        if i <= last_exit:                    # position still open -> cannot enter (1-at-a-time)
            continue
        dt, hi, lo, c = bars[i]; win = bars[i - wb.SWING_K:i + 1]
        ih = hi >= max(b[1] for b in win); il = lo <= min(b[2] for b in win)
        if ih == il:
            continue
        fade = "Sell" if ih else "Buy"
        stamp = wb.build_stamp(highs, lows, closes, i)
        d = good_spot_decision(fade, stamp, htf_key=HTF, skip_up_buy=True, flip_counter_trend=False)
        if d["action"] != "take":
            continue
        pnl, exit_i = resolve(i, fade == "Sell")
        trades.append((dt, pnl - COST, exit_i))
        last_exit = exit_i
    return trades


def main():
    trades = build_trades()
    if not trades:
        print("no trades"); return
    pips = [p for _, p, _ in trades]
    wins = [p for p in pips if p > 0]
    n = len(trades); total = sum(pips)
    print("=" * 80)
    print(f"WRSF DRAWDOWN MODEL  ({n} selected trades, 2.4y, SL{wb.SL_P}/TP{wb.TP_P}, cost {COST}p)")
    print(f"sizing: 1R = {RISK_PCT}% of ${ACCOUNT:.0f} = ${RISK_PCT/100*ACCOUNT:.0f}  ({USD_PER_PIP:.2f}$/pip)")
    print("=" * 80)
    print(f"\nwin rate={100*len(wins)/n:.1f}%  avg={total/n:+.2f}p/trade  total={total:+.0f}p "
          f"= {total/wb.SL_P:+.1f}R = ${total*USD_PER_PIP:+,.0f}")

    # equity curve + max drawdown (in pips -> R/$/%)
    cum = peak = maxdd = 0.0; dd_start = 0
    worst_lo = 0
    for p in pips:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > maxdd:
            maxdd = dd
    maxdd_R = maxdd / wb.SL_P
    print(f"\nMAX DRAWDOWN: {maxdd:.0f}p = {maxdd_R:.1f}R = ${maxdd*USD_PER_PIP:,.0f} "
          f"= {maxdd_R*RISK_PCT:.1f}% of a ${ACCOUNT:.0f} account")

    # longest losing streak (consecutive net-losing trades)
    streak = maxstreak = 0
    for p in pips:
        streak = streak + 1 if p < 0 else 0
        maxstreak = max(maxstreak, streak)
    # losses between wins
    gaps = []; g = 0
    for p in pips:
        if p > 0:
            gaps.append(g); g = 0
        else:
            g += 1
    gaps.sort()
    med_gap = gaps[len(gaps)//2] if gaps else 0
    p95_gap = gaps[int(len(gaps)*0.95)] if gaps else 0
    print(f"longest losing streak: {maxstreak} trades in a row = ${maxstreak*RISK_PCT/100*ACCOUNT:,.0f} "
          f"(-{maxstreak*RISK_PCT:.1f}%) before a win")
    print(f"losses between wins: median={med_gap}  95th pct={p95_gap}  (you wait through this many -1R "
          f"losers per +5R win)")

    # $300 daily-cap interaction
    by_day = defaultdict(list)
    for dt, p, _ in trades:
        by_day[dt.date()].append(p)
    uncapped = sum(pips) * USD_PER_PIP
    capped = 0.0; cap_days = 0; clipped_recovery = 0
    for day in sorted(by_day):
        d = 0.0; hit = False; rest_after_hit = 0.0
        for p in by_day[day]:
            if d <= -DAILY_CAP:
                hit = True; rest_after_hit += p * USD_PER_PIP; continue
            d += p * USD_PER_PIP
        capped += d
        if hit:
            cap_days += 1
            if rest_after_hit > 0:
                clipped_recovery += rest_after_hit
    print(f"\n$300 DAILY CAP: hit on {cap_days}/{len(by_day)} trading days ({100*cap_days/len(by_day):.1f}%)")
    print(f"  P&L with cap = ${capped:,.0f}   vs   uncapped ${uncapped:,.0f}   "
          f"(cap {'HELPS' if capped>uncapped else 'CLIPS recoveries'}: {capped-uncapped:+,.0f})")
    print(f"  recovery $ clipped by the cap (winners skipped after cap hit): ${clipped_recovery:,.0f}")

    print("\nREAD: a 1:5 engine WILL show long red strings. Confirm the account + $300 cap can sit")
    print("through the max drawdown and losing streak above before arming (Phase D).")


if __name__ == "__main__":
    main()
