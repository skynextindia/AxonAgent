import json, statistics
from collections import defaultdict, Counter

rows=[json.loads(l) for l in open('reports/trade_analytics.jsonl') if l.strip()]
def day(r): return r['entry_time'][:10]
def period(r): return 'today' if day(r)=='2026-07-20' else 'prior'
G=defaultdict(list)
for r in rows: G[period(r)].append(r)

print("### FX-ONLY (exclude XAUUSD; gold prices distorted) ###")
for per in ['prior','today']:
    g=[r for r in G[per] if r['symbol']!='XAUUSD']
    n=len(g); net=sum(r['pips_profit'] for r in g)
    w=sum(1 for r in g if r['pips_profit']>0)
    rs=[r['r_multiple'] for r in g if r.get('r_multiple') is not None]
    print(f"  {per}: n={n} netpips={net:.1f} wr={w}/{n}={100*w/n:.1f}% netR={sum(rs):.2f}(r_n={len(rs)})")

print("\n### R by symbol per period (enriched rows only) ###")
for per in ['prior','today']:
    g=G[per]
    bs=defaultdict(list)
    for r in g:
        if r.get('r_multiple') is not None: bs[r['symbol']].append(r['r_multiple'])
    parts=[f"{s}:n={len(v)},sumR={sum(v):.2f},wr={100*sum(1 for x in v if x>0)/len(v):.0f}%" for s,v in sorted(bs.items())]
    print(f"  {per}: "+" | ".join(parts))

print("\n### regime mix per period (count / net pips) ###")
for per in ['prior','today']:
    g=G[per]; rm=defaultdict(lambda:[0,0.0])
    for r in g:
        rm[r['regime']][0]+=1; rm[r['regime']][1]+=r['pips_profit']
    print(f"  {per}:")
    for k,(c,p) in sorted(rm.items(),key=lambda x:-x[1][0]):
        print(f"    {k}: n={c} net={p:.1f} ({100*c/len(g):.0f}%)")

print("\n### displacement_classification mix per period ###")
for per in ['prior','today']:
    g=G[per]; dm=defaultdict(lambda:[0,0.0])
    for r in g:
        dm[r.get('displacement_classification')][0]+=1; dm[r.get('displacement_classification')][1]+=r['pips_profit']
    print(f"  {per}:")
    for k,(c,p) in sorted(dm.items(),key=lambda x:-x[1][0]):
        print(f"    {k}: n={c} net={p:.1f} ({100*c/len(g):.0f}%)")

print("\n### mtf_context mix per period ###")
for per in ['prior','today']:
    g=G[per]; cm=Counter(r.get('mtf_context') for r in g)
    print(f"  {per}: "+" | ".join(f"{k}:{v}" for k,v in cm.most_common()))

print("\n### exit_gate slug (enriched only) per period ###")
for per in ['prior','today']:
    g=[r for r in G[per] if r.get('exit_gate')]
    gm=defaultdict(lambda:[0,0.0])
    for r in g:
        gm[r['exit_gate']][0]+=1; gm[r['exit_gate']][1]+=r['pips_profit']
    print(f"  {per} (n_enriched={len(g)}):")
    for k,(c,p) in sorted(gm.items(),key=lambda x:x[1][1]):
        print(f"    {k}: n={c} net={p:.1f}")
