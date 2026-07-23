import json, statistics as st
rows=[json.loads(l) for l in open('reports/trade_analytics.jsonl')]
def num(x):
    try: return float(x)
    except: return None

# split winners/losers by pips_profit sign
W=[r for r in rows if num(r['pips_profit'])>0]
L=[r for r in rows if num(r['pips_profit'])<0]
F=[r for r in rows if num(r['pips_profit'])==0]
print("W/L/F:",len(W),len(L),len(F))

# initial_sl_pips coverage
sl=[num(r.get('initial_sl_pips')) for r in rows if num(r.get('initial_sl_pips')) is not None]
print("initial_sl_pips n=",len(sl))
if sl:
    print("  sl_pips min/med/mean/max:",round(min(sl),1),round(st.median(sl),1),round(st.mean(sl),1),round(max(sl),1))
    # by symbol
    from collections import defaultdict
    bs=defaultdict(list)
    for r in rows:
        v=num(r.get('initial_sl_pips'))
        if v is not None: bs[r['symbol']].append(v)
    for s,vs in sorted(bs.items()):
        print(f"    {s}: n={len(vs)} med={round(st.median(vs),1)} mean={round(st.mean(vs),1)} range={round(min(vs),1)}-{round(max(vs),1)}")

# r_multiple
R=[num(r.get('r_multiple')) for r in rows if num(r.get('r_multiple')) is not None]
print("r_multiple n=",len(R),"sum=",round(sum(R),2))
Rw=[num(r['r_multiple']) for r in rows if num(r.get('r_multiple')) is not None and num(r['pips_profit'])>0]
Rl=[num(r['r_multiple']) for r in rows if num(r.get('r_multiple')) is not None and num(r['pips_profit'])<0]
print("  R winners n=",len(Rw),"avgWinR=",round(st.mean(Rw),2) if Rw else None)
print("  R losers  n=",len(Rl),"avgLossR=",round(st.mean(Rl),2) if Rl else None)
