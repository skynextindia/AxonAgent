"""A/B: USDJPY hold-for-profit (breakeven-OFF + wide/late trail) vs the current
default (breakeven ratchet at 0.40xATR + tight 0.35xATR trail).

WHY: USDJPY runs entries-off, so its ONLY fills are inverse-mirror legs. Those legs
keep scratching at breakeven (~$0) the moment they pop into profit then revert, while
the EURUSD leg (hold_for_profit ON) rides. This scores whether arming hold_for_profit
for USDJPY too would beat the early-cut default.

METHOD (clean isolation): pull every real USDJPY position from MT5 history, then
re-simulate BOTH exit policies on the SAME per-trade M1 price path from the real
entry. The only thing that differs between arms is the exit params, so any model
error is common-mode and cancels in the A-vs-B delta. Sim-A is then validated against
the REAL closed pips as a trust check on the simulator.

Exit model (mirrors daemon.py _manage_trailing_stops), hard 1:1 +/-30p bracket:
  A (default) : breakeven -> entry +/-1p once profit >= be*ATR (be=0.40); ATR trail
                arms at 0.50*ATR, trails 0.35*ATR behind peak. (Structure-trail is ON
                live but only refines the winners' trail after BE-lock; approximated
                by the ATR trail here -- see CAVEAT. Validated vs real below.)
  B (hold)    : NO breakeven (be=0.0); ATR trail arms LATER at 1.0*ATR, trails WIDER
                at 0.6*ATR behind peak.
Intrabar: ADVERSE-first (conservative). A favorable-first pass bounds the ordering
artifact (bar-resolution lesson: tight trail geometry is order-sensitive).

    .venv/Scripts/python.exe -m research.usdjpy_holdprofit_ab.ab_sim [start=2026-07-01] [hours=24]
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import statistics as st

import MetaTrader5 as mt5

PIP = 0.01                      # USDJPY
HARD = 30.0                     # hard_stop_pips (SL = TP)
A = dict(be=0.40, arm=0.50, trail=0.35)     # current default
B = dict(be=0.00, arm=1.00, trail=0.60)     # hold_for_profit


def atr14_h1_at(sym, epoch):
    """ATR-14 on H1 ending at/just before the entry epoch (what the daemon reads)."""
    rates = mt5.copy_rates_from(sym, mt5.TIMEFRAME_H1, datetime.fromtimestamp(epoch, tz=timezone.utc), 20)
    if rates is None or len(rates) < 15:
        return 0.15                       # fallback ~15 USDJPY pips
    trs = []
    for i in range(1, len(rates)):
        h, l, pc = rates[i]["high"], rates[i]["low"], rates[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-14:]) / 14.0


def simulate(is_buy, entry, atr, m1, params, adverse_first=True):
    """Walk M1 bars from entry; return (exit_pips, reason). +/- in pips."""
    hard_sl = entry - HARD * PIP if is_buy else entry + HARD * PIP
    hard_tp = entry + HARD * PIP if is_buy else entry - HARD * PIP
    sl = hard_sl
    peak = entry                                   # best price reached
    be, arm, trail = params["be"] * atr, params["arm"] * atr, params["trail"] * atr

    def pips(px):
        return ((px - entry) if is_buy else (entry - px)) / PIP

    for bar in m1:
        hi, lo = bar["high"], bar["low"]
        # order the two intrabar extremes
        adverse = lo if is_buy else hi             # worst for us
        favor = hi if is_buy else lo               # best for us
        seq = [("adv", adverse), ("fav", favor)] if adverse_first else [("fav", favor), ("adv", adverse)]
        for kind, px in seq:
            if kind == "adv":
                # stop hit?
                if (is_buy and px <= sl) or (not is_buy and px >= sl):
                    return pips(sl), ("HARD_SL" if abs(sl - hard_sl) < 1e-9 else
                                      ("BE" if abs(pips(sl)) <= 1.5 else "TRAIL"))
            else:
                # hard TP hit?
                if (is_buy and px >= hard_tp) or (not is_buy and px <= hard_tp):
                    return pips(hard_tp), "HARD_TP"
        # update peak + ratchet SL (tighten-only), using the bar's favorable extreme
        peak = max(peak, favor) if is_buy else min(peak, favor)
        prof = pips(favor)                          # best profit seen this bar (pips)
        peak_prof = pips(peak)
        # breakeven park
        if be > 0 and prof >= be / PIP:
            be_sl = entry + PIP if is_buy else entry - PIP
            sl = max(sl, be_sl) if is_buy else min(sl, be_sl)
        # ATR trail
        if peak_prof >= arm / PIP:
            t_sl = peak - trail if is_buy else peak + trail
            sl = max(sl, t_sl) if is_buy else min(sl, t_sl)
    # ran out of path -> mark-to-last close (open trade at horizon)
    return pips(m1[-1]["close"]), "OPEN_EOH"


def summ(tag, pl):
    if not pl:
        print("  %-26s n=0" % tag); return
    n = len(pl)
    print("  %-26s n=%3d  sum%+8.1f  mean%+6.2f  median%+6.2f  win%3.0f%%" % (
        tag, n, sum(pl), st.mean(pl), st.median(pl),
        100 * sum(1 for x in pl if x > 0) / n))


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2026-07-01"
    hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24
    if not mt5.initialize():
        print("MT5 init fail", mt5.last_error()); return 1
    sym = "USDJPY.i"
    frm = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    deals = mt5.history_deals_get(frm, now)
    bypos = defaultdict(list)
    for d in (deals or []):
        if d.symbol.upper().startswith("USDJPY"):
            bypos[d.position_id].append(d)

    trades = []
    for pid, ds in bypos.items():
        ds.sort(key=lambda x: x.time_msc)
        ins = [d for d in ds if d.entry == 0]
        outs = [d for d in ds if d.entry == 1]
        if not ins or not outs:
            continue
        i = ins[0]
        is_buy = (i.type == 0)
        entry = i.price
        ep = i.time
        real_pips = ((outs[-1].price - entry) if is_buy else (entry - outs[-1].price)) / PIP
        m1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1,
                                  datetime.fromtimestamp(ep, tz=timezone.utc),
                                  datetime.fromtimestamp(ep + hours * 3600, tz=timezone.utc))
        if m1 is None or len(m1) < 2:
            continue
        atr = atr14_h1_at(sym, ep)
        trades.append((pid, is_buy, entry, ep, atr, list(m1), real_pips,
                       datetime.fromtimestamp(ep, tz=timezone.utc).strftime("%Y-%m")))
    mt5.shutdown()

    print("USDJPY hold-for-profit A/B  |  %d trades from %s  |  M1 path, %dh cap" % (len(trades), start, hours))
    print("A=default(be0.40/arm0.50/trail0.35)  B=hold(be0.00/arm1.0/trail0.6)  hard+/-30p  adverse-first\n")

    def run(subset, label):
        realp = [t[6] for t in subset]
        a = [simulate(t[1], t[2], t[4], t[5], A)[0] for t in subset]
        b = [simulate(t[1], t[2], t[4], t[5], B)[0] for t in subset]
        af = [simulate(t[1], t[2], t[4], t[5], A, adverse_first=False)[0] for t in subset]
        bf = [simulate(t[1], t[2], t[4], t[5], B, adverse_first=False)[0] for t in subset]
        print("== %s ==" % label)
        summ("REAL closed (policy A)", realp)
        summ("SIM A (validate vs real)", a)
        summ("SIM B (hold-for-profit)", b)
        print("  delta B-A (adverse-first): sum %+.1f  mean %+.2f pips/trade" % (sum(b) - sum(a), st.mean(b) - st.mean(a)))
        print("  favorable-first sensitivity: A sum %+.1f  B sum %+.1f  delta %+.1f" % (sum(af), sum(bf), sum(bf) - sum(af)))
        # exit-reason mix
        ar = defaultdict(int); br = defaultdict(int)
        for t in subset:
            ar[simulate(t[1], t[2], t[4], t[5], A)[1]] += 1
            br[simulate(t[1], t[2], t[4], t[5], B)[1]] += 1
        print("  A exits:", dict(ar))
        print("  B exits:", dict(br), "\n")

    run(trades, "ALL since %s (n=%d)" % (start, len(trades)))
    recent = [t for t in trades if t[7] >= "2026-08"]
    run(recent, "AUG+SEP only (mirror-era, stable config, n=%d)" % len(recent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
