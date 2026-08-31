"""Fetch REAL daily candles from MT5 (read-only) and save to CSV.

yfinance EURUSD=X has a synthetic daily Open (Open~=Close). MT5 daily bars carry
a true broker open, so gap/body stats are meaningful. This ONLY reads market data
(copy_rates) — it never touches orders/positions.

    python -m research.daily_candle_stats.fetch_mt5_daily
"""
from __future__ import annotations
import csv, os, sys
from datetime import datetime, timezone

SYMBOL = "EURUSD.i"
N_BARS = 1200                     # ~4-5y of trading days
_THIS = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(_THIS, "eurusd_d1_mt5.csv")
MT5_PATH = r"C:\Program Files\Eightcap Global MT5 Terminal\terminal64.exe"


def main() -> int:
    try:
        import MetaTrader5 as mt5
    except Exception as e:
        print(f"MetaTrader5 package not importable: {e}")
        return 2

    ok = mt5.initialize(MT5_PATH) or mt5.initialize()
    if not ok:
        print(f"mt5.initialize failed: {mt5.last_error()}")
        return 3
    try:
        info = mt5.terminal_info()
        print(f"connected: {getattr(info,'name',None)} build {getattr(info,'build',None)} "
              f"connected={getattr(info,'connected',None)}")
        # try the .i symbol first, then a bare fallback
        sym = SYMBOL
        if mt5.symbol_info(sym) is None:
            for alt in ("EURUSD", "EURUSD.r", "EURUSD.raw"):
                if mt5.symbol_info(alt) is not None:
                    sym = alt; break
        mt5.symbol_select(sym, True)
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, N_BARS)
        if rates is None or len(rates) == 0:
            print(f"no rates for {sym}: {mt5.last_error()}")
            return 4
        with open(OUT, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Date", "Open", "High", "Low", "Close", "TickVolume"])
            for r in rates:
                d = datetime.fromtimestamp(int(r["time"]), tz=timezone.utc)
                w.writerow([d.strftime("%Y-%m-%d"), f"{r['open']:.5f}", f"{r['high']:.5f}",
                            f"{r['low']:.5f}", f"{r['close']:.5f}", int(r["tick_volume"])])
        print(f"symbol={sym}  wrote {len(rates)} daily bars -> {OUT}")
        print(f"range {datetime.fromtimestamp(int(rates[0]['time']),tz=timezone.utc).date()}"
              f" .. {datetime.fromtimestamp(int(rates[-1]['time']),tz=timezone.utc).date()}")
        # quick body/gap sanity (the whole point)
        bodies = [abs(r["close"] - r["open"]) / 0.0001 for r in rates]
        print(f"|Open-Close| median {sorted(bodies)[len(bodies)//2]:.1f}p  "
              f"mean {sum(bodies)/len(bodies):.1f}p  (real bars should be ~30-80p, not ~0)")
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
