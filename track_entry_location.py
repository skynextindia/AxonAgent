"""Entry-location tracker — is the 'buy-at-top / sell-at-bottom' problem staying closed?

READ-ONLY. Never opens, modifies, or closes a trade. Pulls its own M15 bars from MT5
(bare initialize -> attaches to the running terminal), joins executed entries in
reports/signals.jsonl to their closes, and reports where each entry sat in its recent
range. Run at the 2026-08-18 checkpoint (and beyond):  .venv\\Scripts\\python.exe track_entry_location.py

What to watch (see memory [[entry-location-top-bottom]]):
  * EURUSD BUY-a-top median pips must stay >= 0 on post-2026-08-12 rows
    (the historical bug was median -0.85, 34/36 pre-Aug-11; fixes armed Aug 11-12).
  * SELL entries should keep landing at mean range_pos >= 0.55 (near the TOP = correct).
  * Ignore net-pips headlines; judge on MEDIAN (net-pips here is outlier-driven).

Clock calibration: CSV/MT5 bar time is SERVER time; trigger_candle.open_time is UTC.
This script pulls bars via MT5 directly (already server-time), and entry bar times are
matched by the SAME server clock, so no manual offset is needed here.
"""
import json, os, sys, bisect, statistics as st

REPO = os.path.dirname(os.path.abspath(__file__))
LOOK = 40                      # M15 bars (~10h) for the range window
SPLIT = "2026-08-12"           # fixes fully armed (falling-knife + direction_aware + structure_veto)
SYMS = [("EURUSD", ["EURUSD.i", "EURUSD"]), ("USDJPY", ["USDJPY.i", "USDJPY"])]

def pull_bars():
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("MT5 initialize failed:", mt5.last_error()); sys.exit(1)
    out = {}
    for canon, cands in SYMS:
        rates = None
        for name in cands:
            mt5.symbol_select(name, True)
            r = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_M15, 0, 6000)
            if r is not None and len(r):
                rates = r; break
        if rates is None:
            print(f"WARN: no bars for {canon}"); continue
        out[canon] = sorted((int(x['time']), float(x['open']), float(x['high']),
                             float(x['low']), float(x['close'])) for x in rates)
    mt5.shutdown()
    return out

def load_rows():
    rows = []
    with open(os.path.join(REPO, "reports", "signals.jsonl"), encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try: rows.append(json.loads(ln))
                except Exception: pass
    return rows

def canon_of(mt5_symbol):
    if not mt5_symbol: return None
    return "EURUSD" if mt5_symbol.startswith("EURUSD") else ("USDJPY" if mt5_symbol.startswith("USDJPY") else None)

def main():
    bars = pull_bars()
    if not bars:
        print("No bars pulled; is a terminal running?"); return
    T = {s: [x[0] for x in b] for s, b in bars.items()}
    rows = load_rows()
    ent = [r for r in rows if r.get("event_type") == "peak_detection"
           and (r.get("trade_result") or {}).get("retcode") == 10009 and r.get("decision")]
    closes = {r["ticket"]: r for r in rows if r.get("type") == "trade_closed" and r.get("ticket") is not None}

    # entry server-time: trade_result has no time, so use the entry bar via trigger_candle.open_time.
    # open_time is UTC; MT5 bars are server(UTC+3). Detect the offset empirically per symbol by
    # matching trigger OHLC (robust to broker DST), fall back to +10800.
    def detect_offset(canon):
        b = bars[canon]; tt = T[canon]; idx = {t: i for i, t in enumerate(tt)}
        from collections import Counter
        c = Counter()
        for r in ent:
            if canon_of(r.get("mt5_symbol")) != canon: continue
            tc = (r.get("event_details") or {}).get("trigger_candle") or {}
            ot = tc.get("open_time")
            if ot is None or tc.get("open") is None: continue
            for off in (10800, 7200, 14400, 3600, 0):
                i = idx.get(int(ot) + off)
                if i is not None and abs(b[i][1] - tc["open"]) < (0.01 if canon == "USDJPY" else 1e-4):
                    c[off] += 1; break
        return c.most_common(1)[0][0] if c else 10800

    OFF = {canon: detect_offset(canon) for canon, _ in SYMS if canon in bars}

    def rng_pos(canon, ot, entry):
        b = bars[canon]; i = bisect.bisect_left(T[canon], int(ot) + OFF[canon])
        if i < LOOK: return None
        win = b[i - LOOK:i]; hi = max(x[2] for x in win); lo = min(x[3] for x in win)
        return None if hi - lo <= 0 else (entry - lo) / (hi - lo)

    recs = []
    for r in ent:
        canon = canon_of(r.get("mt5_symbol"))
        if canon not in bars: continue
        tc = (r.get("event_details") or {}).get("trigger_candle") or {}
        ot = tc.get("open_time"); e = (r.get("trade_result") or {}).get("price")
        if ot is None or e is None: continue
        pos = rng_pos(canon, int(ot), float(e))
        if pos is None: continue
        side = "BUY" if r.get("decision") == "Buy" else "SELL"
        cl = closes.get((r.get("trade_result") or {}).get("order"))
        pips = (cl.get("pips", 0) or 0) if cl else None
        recs.append(dict(canon=canon, side=side, pos=pos, pips=pips,
                         ts=r.get("timestamp_utc") or r.get("timestamp") or ""))

    print("=" * 118)
    print(f"ENTRY-LOCATION TRACKER   lookback={LOOK} M15 bars   clock-offset(s)={OFF}   split={SPLIT}")
    print("  correct: SELL near top (pos->1), BUY near bottom (pos->0).  wrong-loc: SELL pos<0.5 / BUY pos>0.5")
    print("=" * 118)

    def block(title, rs):
        rs = [r for r in rs if r["pips"] is not None]
        print(f"\n--- {title}  (n={len(rs)}) ---")
        for canon, _ in SYMS:
            for side in ("SELL", "BUY"):
                x = [r for r in rs if r["canon"] == canon and r["side"] == side]
                if not x:
                    print(f"  {canon} {side:4s}: n=0"); continue
                mpos = sum(r["pos"] for r in x) / len(x)
                wrong = [r for r in x if (r["pos"] < 0.5) == (side == "SELL")]
                ps = [r["pips"] for r in x]; wr = [r["pips"] for r in wrong]
                med = st.median(ps)
                wmed = st.median(wr) if wr else float("nan")
                flag = ""
                if side == "SELL" and mpos < 0.55: flag = "  <-- WATCH: sells not reaching tops"
                if side == "BUY" and canon == "EURUSD" and wr and wmed < 0: flag = "  <-- WATCH: buy-a-top median negative (the bug)"
                print(f"  {canon} {side:4s}: n={len(x):3d}  mean_pos={mpos:.2f}  median={med:+.2f}p  |  "
                      f"wrong-loc n={len(wrong):3d} median={wmed:+.2f}p{flag}")

    block("ALL HISTORY", recs)
    block(f"POST {SPLIT} (fixes armed)", [r for r in recs if r["ts"] >= SPLIT])
    print("\n(Judge on MEDIAN, not net. Baseline bug: EURUSD BUY-a-top median -0.85, 34/36 pre-2026-08-11.)")

if __name__ == "__main__":
    main()
