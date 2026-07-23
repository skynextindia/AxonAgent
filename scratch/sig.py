import json, math
rows=[json.loads(l) for l in open('reports/trade_analytics.jsonl') if l.strip()]
def per(r): return 'today' if r['entry_time'][:10]=='2026-07-20' else 'prior'
G={'today':[],'prior':[]}
for r in rows: G[per(r)].append(r)

# win-rate two-proportion z-test (all trades, win=pips>0)
def wr(g): 
    w=sum(1 for r in g if r['pips_profit']>0); return w,len(g)
w1,n1=wr(G['today']); w2,n2=wr(G['prior'])
p1,p2=w1/n1,w2/n2; p=(w1+w2)/(n1+n2)
se=math.sqrt(p*(1-p)*(1/n1+1/n2)); z=(p1-p2)/se
print(f"WIN RATE today {p1:.3f}(n={n1}) vs prior {p2:.3f}(n={n2}) z={z:.2f} p~{2*(1-0.5*(1+math.erf(abs(z)/math.sqrt(2)))):.3f}")

# FX-only pips: mean per trade + t-ish
def fx(g): return [r['pips_profit'] for r in g if r['symbol']!='XAUUSD']
import statistics as st
a=fx(G['today']); b=fx(G['prior'])
ma,mb=st.mean(a),st.mean(b)
sa,sb=st.pstdev(a),st.pstdev(b)
se2=math.sqrt(sa**2/len(a)+sb**2/len(b)); t=(ma-mb)/se2
print(f"FX mean pips/trade today {ma:.2f}(n={len(a)}) vs prior {mb:.2f}(n={len(b)}) welch-t={t:.2f}")

# ALL pips mean (incl gold)
aa=[r['pips_profit'] for r in G['today']]; bb=[r['pips_profit'] for r in G['prior']]
print(f"ALL mean pips/trade today {st.mean(aa):.1f} vs prior {st.mean(bb):.1f}")

# gold per-trade
ga=[r['pips_profit'] for r in G['today'] if r['symbol']=='XAUUSD']
gb=[r['pips_profit'] for r in G['prior'] if r['symbol']=='XAUUSD']
print(f"GOLD mean pips/trade today {st.mean(ga):.1f}(n={len(ga)}) vs prior {st.mean(gb):.1f}(n={len(gb)}) [gold prices distorted]")
