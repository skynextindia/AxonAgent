import MetaTrader5 as mt5
import time
from datetime import datetime, timezone

if not mt5.initialize():
    print("MT5 initialization failed:", mt5.last_error())
    exit()

symbol = "EURUSDm"

# The log said: "Set last tick time to 2026-05-27 12:40:40"
# Which corresponds to last_tick_time_msc = 1779885640000 (UTC)
last_tick_time_msc = 1779885640000

print(f"Starting test poll loop from last_tick_time_msc = {last_tick_time_msc} ({datetime.utcfromtimestamp(last_tick_time_msc/1000)})")

for i in range(5):
    from_sec = int(last_tick_time_msc // 1000)
    ticks = mt5.copy_ticks_from(symbol, from_sec, 1000, mt5.COPY_TICKS_ALL)
    
    if ticks is None:
        print(f"Poll {i}: ticks is None")
        ticks_len = 0
    else:
        ticks_len = len(ticks)
        print(f"Poll {i}: copy_ticks_from returned {ticks_len} ticks since {from_sec}")
        
    new_ticks = []
    if ticks_len > 0:
        for t in ticks:
            tick_msc = int(t['time_msc'])
            if tick_msc > last_tick_time_msc:
                new_ticks.append(t)
        
        print(f"Poll {i}: {len(new_ticks)} new ticks after filtering (msc > {last_tick_time_msc})")
        if new_ticks:
            last_tick_time_msc = int(new_ticks[-1]['time_msc'])
            print(f"Poll {i}: Updated last_tick_time_msc to {last_tick_time_msc} ({datetime.utcfromtimestamp(last_tick_time_msc/1000)})")
            print(f"Poll {i}: Latest bid={new_ticks[-1]['bid']}, ask={new_ticks[-1]['ask']}")
            
    time.sleep(0.5)

mt5.shutdown()
