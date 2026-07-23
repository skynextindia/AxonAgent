import json, math
rows=[json.loads(l) for l in open('reports/trade_analytics.jsonl')]

def sl_pips(r):
    # initial_sl_pips only on enriched; else derive
    return r.get('initial_sl_pips')

# Build a gate key that spans all 105: prefer exit_gate slug, else derive from exit_reason prefix
def gate_key(r):
    g=r.get('exit_gate')
    if g: return g
    er=(r.get('exit_reason') or '').lower()
    if er.startswith('thesis'): return 'thesis_failure'
    if er.startswith('adverse'): return 'adverse_impulse'
    if er.startswith('exhaust'): return 'exhaustion'
    return 'other:'+er[:30]

# Check consistency of exit_gate vs exit_reason on the 44 enriched
print("=== exit_gate slug distribution (enriched 44) ===")
from collections import Counter
gc=Counter(r.get('exit_gate') for r in rows if r.get('exit_gate'))
print(gc)
print("=== exit_reason prefixes (all 105) ===")
rc=Counter((r.get('exit_reason') or '')[:35] for r in rows)
for k,v in rc.most_common(): print(f"  {v:3d}  {k}")

# On enriched rows, cross-tab gate vs reason to confirm mapping
print("\n=== gate vs reason-prefix crosstab (enriched only) ===")
ct=Counter()
for r in rows:
    if r.get('exit_gate'):
        ct[(r['exit_gate'], (r.get('exit_reason') or '')[:25])]+=1
for k,v in sorted(ct.items()): print(f"  {v:3d}  {k}")
