from tick_engine import TickProcessor, Tick

class MockTick:
    def __init__(self, mid, volume, time):
        self.mid = mid
        self.volume = volume
        self.time = time

def _calculate_order_imbalance(ticks):
    if len(ticks) < 2:
        return {'imbalance_10s': 0.0}
    
    buy_vol = sum(ticks[i].volume for i in range(1, len(ticks)) if ticks[i].mid > ticks[i-1].mid)
    sell_vol = sum(ticks[i].volume for i in range(1, len(ticks)) if ticks[i].mid < ticks[i-1].mid)
    total = buy_vol + sell_vol
    val = (buy_vol - sell_vol) / total if total > 0 else 0.0
    return {'imbalance_10s': val}

# Test 1: pure buying - should return +1.0 not 2.00
ticks = [MockTick(1.1600 + i*0.0001, 1, i) for i in range(5)]
# Test 2: pure selling - should return -1.0
ticks_sell = [MockTick(1.1600 - i*0.0001, 1, i) for i in range(5)]
# Test 3: balanced - should return 0.0
ticks_balanced = [MockTick(1.1600 + (0.0001 if i%2==0 else -0.0001)*i, 1, i) for i in range(6)]
# Test 4: empty - should return 0.0 safely

r1 = _calculate_order_imbalance(ticks)
r2 = _calculate_order_imbalance(ticks_sell)
r3 = _calculate_order_imbalance(ticks_balanced)
r4 = _calculate_order_imbalance([])

print('Pure buying  10s:', r1['imbalance_10s'], '— expected +1.0')
print('Pure selling 10s:', r2['imbalance_10s'], '— expected -1.0')
print('Balanced     10s:', r3['imbalance_10s'], '— expected ~0.0')
print('Empty             :', r4['imbalance_10s'], '— expected 0.0')
print()
print('All values bounded between -1 and +1:', all(
    -1.0 <= v <= 1.0 
    for r in [r1, r2, r3, r4] 
    for k, v in r.items() 
    if k.startswith('imbalance')
))
