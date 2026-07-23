import json
from collections import defaultdict
rows=[json.loads(l) for l in open('reports/trade_analytics.jsonl')]
def gate(r):
    er=(r.get('exit_reason') or '').lower()
    if er.startswith('thesis'): return 'thesis_failure'
    if er.startswith('adverse'): return 'adverse_impulse'
    if er.startswith('exhaust'): return 'exhaustion'
    return 'other'

# MFE unit sanity: show MFE, sl_pips, pips_profit, r_multiple for enriched thesis_failure rows
print("MFE / sl_pips / pips / R  (enriched thesis_failure, verify MFE is in pips)")
for r in rows:
    if gate(r)=='thesis_failure' and r.get('r_multiple') is not None:
        print(f"  {r['symbol']:7} MFE={r['max_favorable_excursion']:7.2f} slp={r.get('initial_sl_pips'):6.1f} pips={r['pips_profit']:+7.1f} R={r['r_multiple']:+.2f} MAE={r.get('max_adverse_excursion')}")

# Gold vs FX split per gate, pips + R
print("\nGold-vs-FX contribution per gate (net pips ; net R on enriched)")
for g in ['thesis_failure','adverse_impulse','exhaustion']:
    for grp,cond in [('GOLD',lambda r:r['symbol']=='XAUUSD'),('FX',lambda r:r['symbol']!='XAUUSD')]:
        rs=[r for r in rows if gate(r)==g and cond(r)]
        net=sum(r['pips_profit'] for r in rs)
        rm=[r['r_multiple'] for r in rs if r.get('r_multiple') is not None]
        w=sum(1 for r in rs if r['pips_profit']>0)
        print(f"  {g:16} {grp:5} n={len(rs):2} net_pips={net:+8.1f}  netR={sum(rm):+.2f}(r_n={len(rm)})  W={w}")

# Is adverse_impulse damage a few big trades or systematic? Distribution of R
print("\nadverse_impulse R distribution (enriched):")
ai=[r['r_multiple'] for r in rows if gate(r)=='adverse_impulse' and r.get('r_multiple') is not None]
ai.sort()
print("  ", [round(x,2) for x in ai])
print(f"  worst={min(ai):.2f} best={max(ai):.2f} median={ai[len(ai)//2]:.2f} n>0={sum(1 for x in ai if x>0)}")

print("\nthesis_failure R distribution (enriched):")
tf=sorted(r['r_multiple'] for r in rows if gate(r)=='thesis_failure' and r.get('r_multiple') is not None)
print("  ", [round(x,2) for x in tf])

# concentration: does adverse_impulse -4.9R come from few trades?
neg=sorted([x for x in ai if x<0])
print(f"\n  adverse_impulse: top3 worst sum={sum(neg[:3]):.2f} of total {sum(ai):.2f} ({sum(neg[:3])/sum(ai)*100:.0f}%)")
