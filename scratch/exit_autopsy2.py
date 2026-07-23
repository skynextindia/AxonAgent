import json
from collections import defaultdict
rows=[json.loads(l) for l in open('reports/trade_analytics.jsonl')]

def gate(r):
    er=(r.get('exit_reason') or '').lower()
    if er.startswith('thesis'): return 'thesis_failure'
    if er.startswith('adverse'): return 'adverse_impulse'
    if er.startswith('exhaust'): return 'exhaustion'
    return 'other'

def pipsize(sym):
    return 0.01 if sym=='XAUUSD' else (0.01 if sym=='USDJPY' else 0.0001)

# MFE stored in pips (per recon, 0..456.2). Convert MFE to R using initial_sl_pips where available.
G=defaultdict(list)
for r in rows:
    G[gate(r)].append(r)

def stats(rs, label, fx_only=False):
    if fx_only:
        rs=[r for r in rs if r['symbol']!='XAUUSD']
    n=len(rs)
    if n==0: return
    net=sum(r['pips_profit'] for r in rs)
    w=sum(1 for r in rs if r['pips_profit']>0)
    l=sum(1 for r in rs if r['pips_profit']<0)
    flat=sum(1 for r in rs if r['pips_profit']==0)
    # R only on enriched
    rmults=[r['r_multiple'] for r in rs if r.get('r_multiple') is not None]
    netR=sum(rmults) if rmults else None
    rn=len(rmults)
    # MFE analysis: MFE in pips. Convert to R via initial_sl_pips (enriched) else skip
    mfe_r_list=[]  # (mfe_in_R, final_r, pips)
    for r in rs:
        mfe=r.get('max_favorable_excursion')
        slp=r.get('initial_sl_pips')
        rm=r.get('r_multiple')
        if mfe is not None and slp and slp>0 and rm is not None:
            mfe_r_list.append((mfe/slp, rm, r))
    # winners cut: MFE>=1R but closed <=0
    cut=[x for x in mfe_r_list if x[0]>=1.0 and x[1]<=0]
    cut05=[x for x in mfe_r_list if x[0]>=0.5 and x[1]<=0]
    tag=" [WEAK n<10]" if n<10 else ""
    print(f"\n--- {label}  n={n}{tag}  {'FXonly' if fx_only else 'ALL'}")
    print(f"    net_pips={net:+.1f}  W/L/flat={w}/{l}/{flat}  wr={w/n*100:.1f}%")
    if rmults:
        print(f"    netR={netR:+.2f} (r_n={rn}, avgR={netR/rn:+.3f})")
    if mfe_r_list:
        avg_mfe_r=sum(x[0] for x in mfe_r_list)/len(mfe_r_list)
        print(f"    MFE-in-R sample n={len(mfe_r_list)}: avgMFE={avg_mfe_r:.2f}R")
        print(f"    WINNERS-CUT (MFE>=1R & closed<=0): {len(cut)}/{len(mfe_r_list)} = {len(cut)/len(mfe_r_list)*100:.0f}%")
        print(f"    reached>=0.5R & closed<=0: {len(cut05)}/{len(mfe_r_list)}")
        # R destroyed by these cut trades
        rdest=sum(x[1] for x in cut)
        rgiven=sum(x[0] for x in cut)  # R that was on the table at MFE
        print(f"    R booked by cut trades={rdest:+.2f}, R that was on table (sum MFE)={rgiven:+.2f}")

print("="*70)
print("PER-GATE AUTOPSY (all symbols)")
for g in ['thesis_failure','adverse_impulse','exhaustion']:
    stats(G[g], g)

print("\n"+"="*70)
print("PER-GATE AUTOPSY (FX ONLY — gold prices suspect)")
for g in ['thesis_failure','adverse_impulse','exhaustion']:
    stats(G[g], g, fx_only=True)

# Overall MFE-cut across all enriched
print("\n"+"="*70)
print("ALL GATES combined")
stats(rows,'ALL')
stats(rows,'ALL',fx_only=True)
