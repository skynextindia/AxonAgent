import json, statistics
from collections import defaultdict

rows=[json.loads(l) for l in open("reports/trade_analytics.jsonl") if l.strip()]
print("n=",len(rows))

def isW(r): return r["pips_profit"]>0
enr=[r for r in rows if r.get("r_multiple") is not None]
print("enriched (R available)=",len(enr))

# helper: bucket a numeric into categories
def zbucket(v):
    if v is None: return None
    if v<=-1: return "z<=-1"
    if v<0: return "-1<z<0"
    if v==0: return "z=0"
    if v<1: return "0<z<1"
    return "z>=1"

def confbucket(v):
    if v is None: return None
    if v<0.5: return "<0.5"
    if v<0.65: return "0.5-0.65"
    if v<0.8: return "0.65-0.8"
    return ">=0.8"

def alignbucket(v):
    if v is None: return None
    a=abs(v)
    if a<0.3: return "|align|<0.3 (weak)"
    if a<0.7: return "0.3-0.7 (mod)"
    return ">=0.7 (strong)"

def num(v, edges, labels):
    if v is None: return None
    for e,l in zip(edges,labels):
        if v<e: return l
    return labels[-1]

# feature extractors: return categorical label
def get(r, feat):
    if feat=="regime": return r.get("regime")
    if feat=="regime_confidence": return confbucket(r.get("regime_confidence"))
    if feat=="mtf_alignment": return alignbucket(r.get("mtf_alignment"))
    if feat=="mtf_context": return r.get("mtf_context")
    if feat=="displacement_classification": return r.get("displacement_classification")
    if feat=="at_structure": return str(r.get("at_structure")) if r.get("at_structure") is not None else None
    if feat=="reversal_pressure":
        v=r.get("reversal_pressure")
        if v is None: return None
        return num(v,[0.2,0.4,0.6,0.8],["<0.2","0.2-0.4","0.4-0.6","0.6-0.8",">=0.8"])
    if feat=="signal_quality":
        v=r.get("signal_quality")
        if v is None: return None
        if isinstance(v,str): return v
        return num(v,[0.4,0.6,0.8],["<0.4","0.4-0.6","0.6-0.8",">=0.8"])
    if feat=="anomaly_velocity_z": return zbucket(r.get("anomaly_velocity_z"))
    if feat=="vel_pct":
        v=r.get("vel_pct")
        if v is None: return None
        return num(v,[25,50,75],["<25","25-50","50-75",">=75"])
    if feat=="vel_tick_eff":
        v=r.get("vel_tick_eff")
        if v is None: return None
        return num(v,[0.3,0.6],["<0.3","0.3-0.6",">=0.6"])
    if feat=="active_sweeps":
        v=r.get("active_sweeps")
        if v is None: return None
        return "sweeps>0" if v else "sweeps=0"
    if feat=="liquidity_void": return str(r.get("liquidity_void")) if r.get("liquidity_void") is not None else None
    if feat=="nearest_level_type": return r.get("nearest_level_type")
    return None

feats=["regime","regime_confidence","mtf_alignment","mtf_context","displacement_classification",
"at_structure","reversal_pressure","signal_quality","anomaly_velocity_z","vel_pct","vel_tick_eff",
"active_sweeps","liquidity_void","nearest_level_type"]

# raw values to inspect distributions
print("\n=== RAW value samples (enriched) ===")
for f in ["at_structure","reversal_pressure","signal_quality","vel_pct","vel_tick_eff","active_sweeps","liquidity_void","nearest_level_type"]:
    vals=set(str(r.get(f)) for r in enr)
    print(f, sorted(vals)[:12])

def summarize(subset, feat):
    # group by category; compute over R (enriched) and win rate (all with feature)
    grp=defaultdict(list)
    for r in subset:
        c=get(r,feat)
        if c is None: continue
        grp[c].append(r)
    out=[]
    for c,rr in grp.items():
        n=len(rr)
        w=sum(1 for r in rr if isW(r))
        wr=w/n*100
        rmults=[r["r_multiple"] for r in rr if r.get("r_multiple") is not None]
        netR=sum(rmults) if rmults else None
        avgR=(sum(rmults)/len(rmults)) if rmults else None
        out.append((c,n,w,wr,len(rmults),netR,avgR))
    return out

print("\n\n########## FEATURE ANALYSIS ##########")
for f in feats:
    print(f"\n===== {f} =====")
    # Use enriched set for R; but win rate we can do on all 105 if feature present on all
    allrows=[r for r in rows if get(r,f) is not None]
    rrows=[r for r in enr if get(r,f) is not None]
    covered_all=len(allrows); covered_R=len(rrows)
    print(f"coverage: {covered_all}/105 have feature; {covered_R}/44 have feature+R")
    res=summarize(allrows,f)
    res.sort(key=lambda x:-x[1])
    for c,n,w,wr,rn,netR,avgR in res:
        weak=" [WEAK n<10]" if n<10 else ""
        rstr=f"netR={netR:+.2f} avgR={avgR:+.2f} (rn={rn})" if rn>0 else "no R"
        print(f"  {str(c):28s} n={n:3d} W={w:2d} wr={wr:5.1f}% {rstr}{weak}")
