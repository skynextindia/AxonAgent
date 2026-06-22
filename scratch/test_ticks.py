import pandas as pd
from datetime import datetime, timedelta
import numpy as np

csv_path = "eurusd_m15_may2026.csv"
df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
if df.index.tz is not None:
    df.index = df.index.tz_convert('UTC').tz_localize(None)
df.sort_index(inplace=True)

candle_rows = []
for idx, row in df.iterrows():
    candle_rows.append({
        "time": idx,
        "open": row["Open"],
        "high": row["High"],
        "low": row["Low"],
        "close": row["Close"],
        "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else 100,
    })

rng = np.random.default_rng(42)
ticks_list = []
for c in candle_rows:
    o, h, l, c_price = c["open"], c["high"], c["low"], c["close"]
    t = c["time"]
    n_ticks = 15
    half_spread = 0.00005

    if c_price >= o:
        seg1 = np.linspace(o, l, int(n_ticks * 0.27), endpoint=False)
        seg2 = np.linspace(l, h, int(n_ticks * 0.40), endpoint=False)
        seg3 = np.linspace(h, c_price, n_ticks - len(seg1) - len(seg2))
    else:
        seg1 = np.linspace(o, h, int(n_ticks * 0.27), endpoint=False)
        seg2 = np.linspace(h, l, int(n_ticks * 0.40), endpoint=False)
        seg3 = np.linspace(l, c_price, n_ticks - len(seg1) - len(seg2))

    tick_prices = np.concatenate([seg1, seg2, seg3])[:n_ticks]
    spread_jitter = rng.uniform(-0.00001, 0.00001, n_ticks)

    candle_close_time = t + timedelta(minutes=15)
    for i, price in enumerate(tick_prices):
        tick_time = candle_close_time - timedelta(seconds=(n_ticks - 1 - i) * 0.05)
        hs = half_spread + spread_jitter[i]
        ticks_list.append((price - hs, price + hs, tick_time))

print(f"Total candles: {len(candle_rows)}")
print(f"Total ticks: {len(ticks_list)}")

# Check if ticks are strictly increasing
out_of_order = 0
for idx in range(1, len(ticks_list)):
    if ticks_list[idx][2] < ticks_list[idx-1][2]:
        out_of_order += 1
        if out_of_order <= 5:
            print(f"Tick out of order at index {idx}: {ticks_list[idx-1][2]} -> {ticks_list[idx][2]}")

print(f"Number of out-of-order ticks: {out_of_order}")
