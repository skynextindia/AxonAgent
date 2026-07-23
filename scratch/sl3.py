import json, statistics as st
from collections import defaultdict
rows=[json.loads(l) for l in open('reports/trade_analytics.jsonl')]
def num(x):
    try: return float(x)
    except: return None
def cat(r):
    p=num(r['pips_profit']); return 'W' if p>0 else ('F' if p==0 else 'L')

# Derive R-per-pip from engine's own r_multiple (consistent, convention-free)
enr=[r for r in rows if num(r.get('r_multiple')) is not None and num(r['pips_profit'])!=0]
print("enriched w/ R and nonzero pips:",len(enr))
for r in enr:
    r['_rpp']=num(r['r_multiple'])/num(r['pips_profit'])
    r['_mfeR']=num(r['max_favorable_excursion'])*r['_rpp']
    m=num(r.get('max_adverse_excursion'))
    r['_maeR']=(-abs(m))*r['_rpp'] if m is not None else None  # MAE stored positive magnitude, adverse=negative

W=[r for r in enr if cat(r)=='W']; L=[r for r in enr if cat(r)=='L']
print("enriched W/L:",len(W),len(L))
print("avg win R:",round(st.mean([num(r['r_multiple']) for r in W]),3))
print("avg loss R:",round(st.mean([num(r['r_multiple']) for r in L]),3))

# LOSERS: MFE in R -- how many gave back a winner (MFE>=1R) vs never went positive
mfeR_L=[r['_mfeR'] for r in L]
print("\nLOSER MFE(R): med=",round(st.median(mfeR_L),2),"mean=",round(st.mean(mfeR_L),2),"max=",round(max(mfeR_L),2))
b={'<=0 (never fav)':0,'0-0.5R':0,'0.5-1R':0,'>=1R (gave back winner)':0}
for x in mfeR_L:
    if x<=0.001:b['<=0 (never fav)']+=1
    elif x<0.5:b['0-0.5R']+=1
    elif x<1:b['0.5-1R']+=1
    else:b['>=1R (gave back winner)']+=1
print("LOSER MFE(R) buckets:",b)

# payoff math
aw=st.mean([num(r['r_multiple']) for r in W]); al=st.mean([num(r['r_multiple']) for r in L])
print("\n=== PAYOFF (44 enriched) ===")
print("avgWin=%.3fR avgLoss=%.3fR ratio=%.2f"%(aw,al,aw/abs(al)))
# breakeven win rate: wr*aw + (1-wr)*al = 0 -> wr = -al/(aw-al)
be=-al/(aw-al)
print("breakeven WR needed:",round(be*100,1),"% | actual enriched WR:",round(100*len(W)/len(enr),1),"%")

# whole-file pips-based payoff (all 105)
allW=[num(r['pips_profit']) for r in rows if num(r['pips_profit'])>0]
allL=[num(r['pips_profit']) for r in rows if num(r['pips_profit'])<0]
awp=st.mean(allW); alp=st.mean(allL)
bep=-alp/(awp-alp)
print("\n=== PAYOFF (all 105, pips) ===")
print("avgWin=%.1fp (n=%d) avgLoss=%.1fp (n=%d) ratio=%.2f"%(awp,len(allW),alp,len(allL),awp/abs(alp)))
print("breakeven WR needed:",round(bep*100,1),"% | actual WR:",round(100*44/105,1),"%")

# exclude gold (suspect prices) - FX only pips payoff
fx=[r for r in rows if r['symbol']!='XAUUSD']
fW=[num(r['pips_profit']) for r in fx if num(r['pips_profit'])>0]
fL=[num(r['pips_profit']) for r in fx if num(r['pips_profit'])<0]
print("\n=== PAYOFF (FX only, 84 trades, pips) ===")
print("n=%d net=%.1f avgWin=%.2fp(n=%d) avgLoss=%.2fp(n=%d) ratio=%.2f"%(
    len(fx),sum(num(r['pips_profit']) for r in fx),st.mean(fW),len(fW),st.mean(fL),len(fL),st.mean(fW)/abs(st.mean(fL))))
bef=-st.mean(fL)/(st.mean(fW)-st.mean(fL))
print("FX breakeven WR:",round(bef*100,1),"% | actual FX WR:",round(100*len(fW)/len(fx),1),"%")

# WINNERS: how deep MAE before winning
maeR_W=[r['_maeR'] for r in W if r['_maeR'] is not None]
print("\nWINNER MAE(R): med=",round(st.median(maeR_W),2),"min(deepest)=",round(min(maeR_W),2))
