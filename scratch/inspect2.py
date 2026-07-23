import json
from collections import defaultdict
PATH = r"D:/AXON.AI/AxonAgent-Agy/reports/trade_analytics.jsonl"
rows=[]
with open(PATH,encoding="utf-8") as f:
    for i,l in enumerate(f,1):
        l=l.strip()
        if l: rows.append((i,json.loads(l)))

# 1) enriched vs lite split: use presence of r_multiple as the marker
def has(r,k):
    v=r.get(k); return not (v is None or v=="")
enriched=[ln for ln,r in rows if has(r,"r_multiple")]
lite=[ln for ln,r in rows if not has(r,"r_multiple")]
print("enriched (has r_multiple):", len(enriched))
print("lite:", len(lite))

# does enriched==has exit_gate? and MAE?
eg=[ln for ln,r in rows if has(r,"exit_gate")]
mae=[ln for ln,r in rows if has(r,"max_adverse_excursion")]
print("has exit_gate:", len(eg), " same set as r_multiple?", set(eg)==set(enriched))
print("has MAE:", len(mae), " same set as r_multiple?", set(mae)==set(enriched))

# split by day and symbol
print("\nenriched by day/symbol:")
d=defaultdict(int); s=defaultdict(int)
for ln,r in rows:
    if has(r,"r_multiple"):
        d[r.get("entry_time","")[:10]]+=1; s[r.get("symbol")]+=1
print(" day:",dict(d)); print(" sym:",dict(s))
print("\nlite by day/symbol:")
d=defaultdict(int); s=defaultdict(int)
for ln,r in rows:
    if not has(r,"r_multiple"):
        d[r.get("entry_time","")[:10]]+=1; s[r.get("symbol")]+=1
print(" day:",dict(d)); print(" sym:",dict(s))

# net pips within enriched vs lite
def net(subset):
    tot=0; w=l=0
    for ln,r in rows:
        cond = has(r,"r_multiple")
        if (subset=="e")!=cond: continue
        p=float(r.get("pips_profit"))
        tot+=p
        if p>0:w+=1
        elif p<0:l+=1
    return tot,w,l
print("\nenriched net_pips:",net("e"))
print("lite     net_pips:",net("l"))

# 2) the two sign-mismatch rows full detail
print("\n=== MISMATCH ROW 41 ===")
r41 = dict(rows[40][1])
for k in ["symbol","direction","entry_time","exit_time","entry_price","exit_price","initial_sl","initial_tp","pips_profit","r_multiple","max_favorable_excursion","max_adverse_excursion","exit_reason","exit_gate","health_score_at_exit","ticks_in_trade"]:
    print(f"   {k}: {r41.get(k)}")
print("\n=== MISMATCH ROW 42 ===")
r42 = dict(rows[41][1])
for k in ["symbol","direction","entry_time","exit_time","entry_price","exit_price","initial_sl","initial_tp","pips_profit","r_multiple","max_favorable_excursion","max_adverse_excursion","exit_reason","exit_gate"]:
    print(f"   {k}: {r42.get(k)}")

# 3) recompute pips-per-price scale per symbol to understand pip def used
print("\n=== IMPLIED PIP SCALE (median |pips|/|price move|) per symbol ===")
import statistics
bysym=defaultdict(list)
for ln,r in rows:
    try:
        ep=float(r["entry_price"]); xp=float(r["exit_price"]); pp=float(r["pips_profit"])
    except: continue
    mv=abs(xp-ep)
    if mv>0 and abs(pp)>0:
        bysym[r["symbol"]].append(abs(pp)/mv)
for sym,vals in bysym.items():
    print(f"   {sym}: n={len(vals)} median pips-per-price-unit={statistics.median(vals):.2f}  (1 pip = {1/statistics.median(vals):.6g} price)")

# 4) MFE/MAE sanity: are MFE>=0 and MAE<=0 ? and is |pips|<=MFE?
print("\n=== MFE/MAE ranges ===")
mfe=[float(r['max_favorable_excursion']) for ln,r in rows if has(r,'max_favorable_excursion')]
maev=[float(r['max_adverse_excursion']) for ln,r in rows if has(r,'max_adverse_excursion')]
print("MFE min/max:",min(mfe),max(mfe), " negatives:",sum(1 for x in mfe if x<0))
print("MAE min/max:",min(maev),max(maev), " positives:",sum(1 for x in maev if x>0))

# time_in_drawdown, health ranges
tdd=[float(r['time_in_drawdown_sec']) for ln,r in rows if has(r,'time_in_drawdown_sec')]
hs=[float(r['health_score_at_exit']) for ln,r in rows if has(r,'health_score_at_exit')]
print("time_in_drawdown_sec min/max:",min(tdd),max(tdd), " zeros:",sum(1 for x in tdd if x==0))
print("health_score_at_exit min/max:",min(hs),max(hs))

# 5) how does exit_reason free-text prefix map to gate on 105
print("\n=== exit_reason PREFIX buckets across all 105 ===")
buck=defaultdict(lambda:[0,0.0])
for ln,r in rows:
    er=(r.get("exit_reason") or "")
    pre = er.split(":")[0].strip().lower() if er else "(empty)"
    p=float(r.get("pips_profit"))
    buck[pre][0]+=1; buck[pre][1]+=p
for k,(c,pp) in sorted(buck.items(),key=lambda x:x[1][1]):
    print(f"   {k:20s} n={c:3d} net_pips={pp:10.4f}")

# cross-check exit_gate present only on enriched; on lite what is exit_reason?
print("\n=== does lite set have exit_gate ever? ===")
print("lite rows with exit_gate:", sum(1 for ln,r in rows if not has(r,'r_multiple') and has(r,'exit_gate')))
