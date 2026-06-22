import MetaTrader5 as mt5
import time
from datetime import datetime

if not mt5.initialize():
    print("MT5 initialization failed:", mt5.last_error())
    exit()

symbol = "EURUSDm"
tick = mt5.symbol_info_tick(symbol)
if tick is None:
    print("Failed to get latest tick")
    exit()

print("Latest Tick MSC:", tick.time_msc)
from_sec = int(tick.time_msc // 1000)
print("Querying copy_ticks_from since:", from_sec)

ticks = mt5.copy_ticks_from(symbol, from_sec, 100, mt5.COPY_TICKS_ALL)
print("Ticks returned:", ticks)
if ticks is not None:
    print("Number of ticks:", len(ticks))
    if len(ticks) > 0:
        print("First tick:", ticks[0])
        print("Last tick:", ticks[-1])

mt5.shutdown()
