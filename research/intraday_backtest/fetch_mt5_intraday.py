"""Fetch REAL intraday candles (M5 + M15) from MT5 (read-only) for the intraday
fade backtest. Never touches orders/positions.

    python -m research.intraday_backtest.fetch_mt5_intraday
"""
from __future__ import annotations
import csv, os
from datetime import datetime, timezone

SYMBOL = "EURUSD.i"
_THIS = os.path.dirname(os.path.abspath(__file__))
MT5_PATH = r"C:\Program Files\Eightcap Global MT5 Terminal\terminal64.exe"
WANT = [("M5", 150000), ("M15", 60000)]


def main() -> int:
    try:
        import MetaTrader5 as mt5
    except Exception as e:
        print(f"MetaTrader5 not importable: {e}")
        return 2
    if not (mt5.initialize(MT5_PATH) or mt5.initialize()):
        print(f"initialize failed: {mt5.last_error()}")
        return 3
    try:
        sym = SYMBOL if mt5.symbol_info(SYMBOL) else "EURUSD"
        mt5.symbol_select(sym, True)
        tf_map = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15}
        for name, count in WANT:
            rates = mt5.copy_rates_from_pos(sym, tf_map[name], 0, count)
            if rates is None or len(rates) == 0:
                print(f"{name}: no rates ({mt5.last_error()})"); continue
            out = os.path.join(_THIS, f"eurusd_{name.lower()}_mt5.csv")
            with open(out, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["Datetime", "Open", "High", "Low", "Close", "TickVolume"])
                for r in rates:
                    d = datetime.fromtimestamp(int(r["time"]), tz=timezone.utc)
                    w.writerow([d.strftime("%Y-%m-%d %H:%M:%S"), f"{r['open']:.5f}",
                                f"{r['high']:.5f}", f"{r['low']:.5f}", f"{r['close']:.5f}",
                                int(r["tick_volume"])])
            d0 = datetime.fromtimestamp(int(rates[0]['time']), tz=timezone.utc).date()
            d1 = datetime.fromtimestamp(int(rates[-1]['time']), tz=timezone.utc).date()
            print(f"{name}: {len(rates)} bars  {d0}..{d1}  -> {os.path.basename(out)}")
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
