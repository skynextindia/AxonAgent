import MetaTrader5 as mt5
import time
from datetime import datetime, timezone

if not mt5.initialize():
    print("MT5 initialization failed:", mt5.last_error())
    exit()

symbol = "EURUSDm"
tick = mt5.symbol_info_tick(symbol)
print("System time (time.time()):", time.time())
print("System UTC now:", datetime.now(timezone.utc))
print("System local now:", datetime.now())

if tick is not None:
    print("Tick time (raw):", tick.time)
    print("Tick time_msc (raw):", tick.time_msc)
    print("Tick time as UTC datetime:", datetime.utcfromtimestamp(tick.time))
    print("Tick time_msc as UTC datetime:", datetime.utcfromtimestamp(tick.time_msc / 1000.0))
    
    # Check if we can copy ticks from now
    import datetime as dt
    now_utc = datetime.utcnow()
    print("Querying copy_ticks_from with UTC now - 10s...")
    ticks = mt5.copy_ticks_from(symbol, now_utc - dt.timedelta(seconds=10), 10, mt5.COPY_TICKS_ALL)
    print("UTC query returned:", len(ticks) if ticks is not None else "None")
    
    print("Querying copy_ticks_from with tick.time - 10s...")
    ticks2 = mt5.copy_ticks_from(symbol, tick.time - 10, 10, mt5.COPY_TICKS_ALL)
    print("Tick.time query returned:", len(ticks2) if ticks2 is not None else "None")
else:
    print("Tick is None")

mt5.shutdown()
