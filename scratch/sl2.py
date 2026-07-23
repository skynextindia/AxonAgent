import json, statistics as st
from collections import defaultdict
rows=[json.loads(l) for l in open('reports/trade_analytics.jsonl')]
def num(x):
    try: return float(x)
    except: return None
pipsize={'XAUUSD':0.1,'USDJPY':0.01,'EURUSD':0.0001,'GBPUSD':0.0001,'AUDUSD':0.0001}
# recompute SL distance in true pips from price geometry
print("=== SL geometry (price-derived) vs stored initial_sl_pips ===")
for r in rows:
    if num(r.get('initial_sl_pips')) is None: continue
for s in ['XAUUSD','EURUSD','GBPUSD','AUDUSD','USDJPY']:
    dd=[]
    for r in rows:
        if r['symbol']!=s: continue
        e=num(r['entry_price']); slp=num(r['initial_sl'])
        stored=num(r.get('initial_sl_pips'))
        if e is None or slp is None: continue
        dist_pips=abs(e-slp)/pipsize[s]
        dd.append((dist_pips,stored))
    if dd:
        gp=[d[0] for d in dd]
        gs=[d[1] for d in dd if d[1] is not None]
        print(f"{s}: n={len(gp)} geomSLpips med={round(st.median(gp),1)} range={round(min(gp),1)}-{round(max(gp),1)} | storedSLpips {'med='+str(round(st.median(gs),1)) if gs else 'NA'}")

# MFE/MAE in R terms using stored initial_sl_pips as risk (44 enriched only)
print("\n=== MFE/MAE analysis on 44 enriched (risk=initial_sl_pips) ===")
enr=[r for r in rows if num(r.get('initial_sl_pips')) is not None]
def cat(r): return 'W' if num(r['pips_profit'])>0 else ('F' if num(r['pips_profit'])==0 else 'L')
losers=[r for r in enr if cat(r)=='L']
print("enriched losers n=",len(losers))
gave_back=0; never_pos=0; mfe_ge_1r=0
for r in losers:
    mfe=num(r['max_favorable_excursion']); risk=num(r['initial_sl_pips'])
    mfe_r=mfe/risk if risk else 0
    if mfe_r>=1.0: mfe_ge_1r+=1
    if mfe<=0.5: never_pos+=1  # essentially never moved favorably
# use MFE>=1R as "gave back a winner"
for r in losers:
    mfe=num(r['max_favorable_excursion']); risk=num(r['initial_sl_pips'])
    if risk and mfe/risk>=1.0: gave_back+=1
print("losers with MFE>=1R (gave back a winner):",gave_back)
print("losers with MFE<=0.5 pips (never went favorable, thesis wrong):",never_pos)

# distribution of MFE in R for losers
mfr=[num(r['max_favorable_excursion'])/num(r['initial_sl_pips']) for r in losers if num(r['initial_sl_pips'])]
print("loser MFE/R: med=",round(st.median(mfr),2),"mean=",round(st.mean(mfr),2),"max=",round(max(mfr),2))
buckets={'<0.5R':0,'0.5-1R':0,'1-2R':0,'>=2R':0}
for x in mfr:
    if x<0.5: buckets['<0.5R']+=1
    elif x<1: buckets['0.5-1R']+=1
    elif x<2: buckets['1-2R']+=1
    else: buckets['>=2R']+=1
print("loser MFE/R buckets:",buckets)

# MAE for winners: how deep did winners dig before winning
winners=[r for r in enr if cat(r)=='W']
mae_w=[num(r['max_adverse_excursion'])/num(r['initial_sl_pips']) for r in winners if num(r.get('max_adverse_excursion')) is not None and num(r['initial_sl_pips'])]
print("\nwinners n=",len(winners),"MAE/R med=",round(st.median(mae_w),2) if mae_w else None,"max=",round(max(mae_w),2) if mae_w else None)
