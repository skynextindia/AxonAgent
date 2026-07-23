import json, statistics
from collections import defaultdict, Counter

rows=[]
with open('reports/trade_analytics.jsonl') as f:
    for line in f:
        line=line.strip()
        if not line: continue
        rows.append(json.loads(line))

def day(r): return r['entry_time'][:10]

def period(r):
    return 'today' if day(r)=='2026-07-20' else 'prior'

groups=defaultdict(list)
for r in rows:
    groups[period(r)].append(r)

def exit_slug(r):
    # use exit_reason free text prefix, spans all 105
    er=(r.get('exit_reason') or '').lower()
    if er.startswith('thesis failure'): return 'thesis_failure'
    if er.startswith('adverse impulse'): return 'adverse_impulse'
    if er.startswith('exhaustion'): return 'exhaustion'
    return er[:30]

for per in ['prior','today']:
    g=groups[per]
    n=len(g)
    net=sum(r['pips_profit'] for r in g)
    wins=sum(1 for r in g if r['pips_profit']>0)
    losses=sum(1 for r in g if r['pips_profit']<0)
    flat=sum(1 for r in g if r['pips_profit']==0)
    rs=[r['r_multiple'] for r in g if r.get('r_multiple') is not None]
    netR=sum(rs) if rs else None
    sq=[r['signal_quality'] for r in g if r.get('signal_quality') is not None]
    mtf=[r['mtf_alignment'] for r in g if r.get('mtf_alignment') is not None]
    print(f"=== {per.upper()} n={n} ===")
    print(f"  net pips={net:.1f}  wr={wins}/{n}={100*wins/n:.1f}%  W/L/flat={wins}/{losses}/{flat}")
    print(f"  netR={netR:.2f} (r_n={len(rs)})" if netR is not None else "  netR=NA")
    print(f"  avg signal_quality={statistics.mean(sq):.3f} (n={len(sq)})" if sq else "  signal_quality NA")
    print(f"  avg mtf_alignment={statistics.mean(mtf):.3f} (n={len(mtf)})" if mtf else "")
    # exit mix
    em=defaultdict(lambda:[0,0.0])
    for r in g:
        s=exit_slug(r); em[s][0]+=1; em[s][1]+=r['pips_profit']
    print("  exit mix (by exit_reason prefix): count / net pips")
    for s,(c,p) in sorted(em.items(),key=lambda x:x[1][1]):
        print(f"    {s}: n={c} net={p:.1f}")
    # symbol mix
    sm=defaultdict(lambda:[0,0.0])
    for r in g:
        sm[r['symbol']][0]+=1; sm[r['symbol']][1]+=r['pips_profit']
    print("  symbol mix: count / net pips")
    for s,(c,p) in sorted(sm.items(),key=lambda x:x[1][1]):
        print(f"    {s}: n={c} net={p:.1f}")
    print()
