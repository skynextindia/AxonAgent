import MetaTrader5 as mt5

if not mt5.initialize():
    print("Failed to initialize MT5")
    exit(1)

symbols = mt5.symbols_get()
if symbols is None:
    print("No symbols found")
    mt5.shutdown()
    exit(1)

print(f"Total symbols: {len(symbols)}")
print("Filtering symbols containing 'EURUSD':")
matched = [s.name for s in symbols if "EURUSD" in s.name.upper()]
for name in matched:
    print(f"  - {name}")

# Print first 20 symbols just to see the suffix naming pattern
print("\nFirst 20 symbols on the broker:")
for s in symbols[:20]:
    print(f"  - {s.name}")

mt5.shutdown()
