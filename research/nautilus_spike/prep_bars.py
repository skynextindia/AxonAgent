"""Convert AxonAI engine_snapshots_{SYM}.csv (tick stream: timestamp, price) into
M15 OHLC bar DataFrames for the NautilusTrader backtest spike.

Nautilus-independent (pure pandas) so it can run in either interpreter. Output
matches the M15 aggregation the offline validator used (900s buckets, last tick
= close, tick count = volume) so the Nautilus run is comparable to the
+6.06p/trade shadow result.

Snapshots carry only mid `price` (no bid/ask), so the backtest models spread and
slippage via Nautilus's FillModel rather than from real quotes -- that IS the
point of the spike: measure the edge under a modeled fill instead of the
offline sim's idealized neckline fill.
"""
import os
import pandas as pd

REPORTS = r"D:\AXON.AI\AxonAgent-Agy\reports"
OUT = os.path.join(os.path.dirname(__file__), "data")
PAIRS = ["EURUSD", "USDJPY", "AUDUSD"]  # GBP excluded (-2.94p OOS)


def to_m15(symbol: str) -> pd.DataFrame:
    path = os.path.join(REPORTS, f"engine_snapshots_{symbol}.csv")
    # Only need timestamp + price; ignore the rest of the wide snapshot schema.
    df = pd.read_csv(path, usecols=["timestamp", "price"], dtype={"price": "float64"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df = df.dropna(subset=["timestamp", "price"])
    df = df[df["price"] > 0].set_index("timestamp").sort_index()
    o = df["price"].resample("15min").first()
    h = df["price"].resample("15min").max()
    lo = df["price"].resample("15min").min()
    c = df["price"].resample("15min").last()
    v = df["price"].resample("15min").count()
    bars = pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": v})
    bars = bars.dropna(subset=["close"])
    bars = bars[bars["volume"] > 0]
    return bars


def main():
    os.makedirs(OUT, exist_ok=True)
    for sym in PAIRS:
        bars = to_m15(sym)
        fp = os.path.join(OUT, f"{sym}_m15.parquet")
        bars.to_parquet(fp)
        print(f"{sym}: {len(bars)} M15 bars {bars.index.min()}..{bars.index.max()} -> {fp}")


if __name__ == "__main__":
    main()
