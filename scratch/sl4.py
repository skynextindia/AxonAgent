import json, statistics as st
from collections import defaultdict
rows=[json.loads(l) for l in open('reports/trade_analytics.jsonl')]
def num(x):
    try: return float(x)
    except: return None
def cat(r):
    p=num(r['pips_profit']); return 'W' if p>0 else ('F' if p==0 else 'L')
enr=[r for r in rows if num(r.get('r_multiple')) is not None and num(r['pips_profit'])!=0]
for r in enr:
    r['_rpp']=num(r['r_multiple'])/num(r['pips_profit'])
    m=num(r.get('max_adverse_excursion'))
    r['_maeR']=(-abs(m))*r['_rpp'] if m is not None else None
L=[r for r in enr if cat(r)=='L']

# Did losers hit full stop? MAE(R) distribution for losers
maeR=[r['_maeR'] for r in L if r['_maeR'] is not None]
print("LOSER MAE(R): med=",round(st.median(maeR),2),"deepest=",round(min(maeR),2))
b={'0 to -0.5R':0,'-0.5 to -0.9R':0,'<=-0.9R (near/full stop)':0}
for x in maeR:
    if x>-0.5:b['0 to -0.5R']+=1
    elif x>-0.9:b['-0.5 to -0.9R']+=1
    else:b['<=-0.9R (near/full stop)']+=1
print("LOSER MAE(R) buckets:",b)
print("avg loser exit R =",round(st.mean([num(r['r_multiple']) for r in L]),3),"vs avg loser deepest MAE R =",round(st.mean(maeR),3))

# exit gate/reason for losers (are they SL hits or lifecycle exits?)
print("\nLOSER exit_reason prefix (all 105 losers):")
allL=[r for r in rows if cat(r)=='L']
c=defaultdict(lambda:[0,0.0])
for r in allL:
    key=r['exit_reason'][:40] if r.get('exit_reason') else 'NA'
    c[key][0]+=1; c[key][1]+=num(r['pips_profit'])
for k,v in sorted(c.items(),key=lambda x:x[1][1]):
    print(f"  n={v[0]:2d} net={v[1]:8.1f}  {k}")

# how many losers exited at a real stop_loss gate
print("\nexit_gate values (44 enriched):")
g=defaultdict(int)
for r in enr: g[r.get('exit_gate')]+=1
for k,v in sorted(g.items()): print(f"  {k}: {v}")

print("\n=== WINNER give-back (are exits cutting winners?) ===")
W=[r for r in enr if cat(r)=='W']
for r in W:
    r['_mfeR']=num(r['max_favorable_excursion'])*r['_rpp']
gb=[(r['_mfeR']-num(r['r_multiple']))/r['_mfeR'] for r in W if r['_mfeR']>0]
mfeW=[r['_mfeR'] for r in W]
exitW=[num(r['r_multiple']) for r in W]
print("winner peak MFE(R) med=",round(st.median(mfeW),2),"| winner exit R med=",round(st.median(exitW),2))
print("winner captured fraction (exitR/MFER) med=",round(st.median([e/m for e,m in zip(exitW,mfeW) if m>0]),2))
