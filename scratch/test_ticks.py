import MetaTrader5 as mt5
import time
from datetime import datetime

if not mt5.initialize():
    print("MT5 initialization failed:", mt5.last_error())
    exit()

symbol = "EURUSDm"
info = mt5.symbol_info(symbol)
if info is None:
    print(f"Symbol {symbol} not found")
    exit()

if not info.visible:
    mt5.symbol_select(symbol, True)

print("Connected to MT5 successfully!")
print("Symbol Info:", info)

# Print latest tick
tick = mt5.symbol_info_tick(symbol)
print("Latest Tick:", tick)
if tick:
    print("Latest Tick Time:", datetime.utcfromtimestamp(tick.time))
    print("Latest Tick Time MSC:", tick.time_msc)

# Try polling for 5 seconds
print("Polling ticks for 5 seconds...")
last_msc = tick.time_msc if tick else 0
start_time = time.time()
while time.time() - start_time < 5:
    tick = mt5.symbol_info_tick(symbol)
    if tick and tick.time_msc > last_msc:
        print(f"NEW TICK: Bid={tick.bid}, Ask={tick.ask}, MSC={tick.time_msc}")
        last_msc = tick.time_msc
    time.sleep(0.5)

mt5.shutdown()
