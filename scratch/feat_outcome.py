# -*- coding: utf-8 -*-
import json, numpy as np
from collections import Counter

rng = np.random.default_rng(12345)
def num(v):
    try:
        if v is None or v=='' or isinstance(v,bool): return float('nan') if isinstance(v,bool) else float('nan')
        return float(v)
    except (TypeError,ValueError): return float('nan')
rows = [json.loads(l) for l in open('reports/trade_analytics.jsonl', encoding='utf-8') if l.strip()]

def is_fx(r): return r['symbol'] != 'XAUUSD'

# outcome definitions
for r in rows:
    r['win'] = 1 if r['pips_profit'] > 0 else 0

fx = [r for r in rows if is_fx(r)]
fx_enr = [r for r in fx if 'r_multiple' in r]
enr_all = [r for r in rows if 'r_multiple' in r]

print('=== COHORTS ===')
print('all 105:', len(rows), 'wins', sum(r['win'] for r in rows), 'winrate %.1f' % (100*sum(r['win'] for r in rows)/len(rows)))
print('FX all:', len(fx), 'wins', sum(r['win'] for r in fx), 'winrate %.1f' % (100*sum(r['win'] for r in fx)/len(fx)))
print('FX enriched:', len(fx_enr), 'wins(pips)', sum(r['win'] for r in fx_enr))
# R-based win on FX enriched
rwin = sum(1 for r in fx_enr if r['r_multiple']>0)
print('FX enriched R>0 wins:', rwin, 'winrate %.1f' % (100*rwin/len(fx_enr)))
Rvals = np.array([r['r_multiple'] for r in fx_enr])
print('FX enriched E[R]=%.4f  meanwin=%.3f meanloss=%.3f' % (
    Rvals.mean(),
    np.mean([r['r_multiple'] for r in fx_enr if r['r_multiple']>0]),
    np.mean([r['r_multiple'] for r in fx_enr if r['r_multiple']<=0])))

# ---- test helpers (permutation, 20000 iters) ----
NPERM = 20000
def perm_num_binary(x, y):
    # x numeric, y binary(0/1). stat = |mean(x|1)-mean(x|0)|
    x=np.asarray(x,float); y=np.asarray(y,int)
    m=~np.isnan(x); x=x[m]; y=y[m]
    if len(np.unique(y))<2 or len(x)<3: return None,None,len(x)
    obs=abs(x[y==1].mean()-x[y==0].mean())
    n1=int(y.sum()); n=len(y)
    cnt=0
    for _ in range(NPERM):
        idx=rng.permutation(n)
        yp=y[idx]
        st=abs(x[yp==1].mean()-x[yp==0].mean())
        if st>=obs-1e-12: cnt+=1
    return obs,(cnt+1)/(NPERM+1),len(x)

def perm_num_cont(x, R):
    # pearson corr of x with R; permutation
    x=np.asarray(x,float); R=np.asarray(R,float)
    m=~np.isnan(x); x=x[m]; R=R[m]
    if len(x)<3 or x.std()==0: return None,None,len(x)
    obs=abs(np.corrcoef(x,R)[0,1])
    cnt=0
    for _ in range(NPERM):
        st=abs(np.corrcoef(x,rng.permutation(R))[0,1])
        if st>=obs-1e-12: cnt+=1
    return obs,(cnt+1)/(NPERM+1),len(x)

def perm_cat_binary(cats, y):
    # cats list of labels, y binary. stat = chi-square-like on winrate
    cats=list(cats); y=np.asarray(y,int)
    labels=sorted(set(cats))
    if len(labels)<2: return None,None,len(y),labels
    idxmap={l:i for i,l in enumerate(labels)}
    c=np.array([idxmap[v] for v in cats])
    def stat(yy):
        s=0.0
        gw=yy.mean()
        for i in range(len(labels)):
            mask=c==i
            n=mask.sum()
            if n==0: continue
            obs=yy[mask].sum(); exp=n*gw
            if exp>0 and exp<n: s+=(obs-exp)**2/(exp*(1-gw))
        return s
    obs=stat(y); cnt=0
    for _ in range(NPERM):
        if stat(rng.permutation(y))>=obs-1e-12: cnt+=1
    return obs,(cnt+1)/(NPERM+1),len(y),labels

NUMERIC = ['regime_confidence','mtf_alignment','anomaly_velocity_z','displacement_ratio',
           'net_displacement_pips','reversal_pressure','distance_to_sr','room_available',
           'active_sweeps','signal_quality','vel_pct','vel_tick_eff','vel_vol_pips',
           'vel_decay_ratio','mtf_h4_bias','mtf_h1_bias','mtf_m15_bias','volatility','initial_sl_pips']
CATEG = ['regime','mtf_context','displacement_classification','nearest_level_type',
         'at_structure','active_breaks','liquidity_void','vel_is_unusual']

print('\n=== CONSTANT / NEAR-CONSTANT on FX enriched (n=%d) ===' % len(fx_enr))
for f in NUMERIC+CATEG:
    vals=[r.get(f) for r in fx_enr if f in r]
    if not vals:
        print('  %-26s ABSENT'%f); continue
    c=Counter(vals); mode,mc=c.most_common(1)[0]
    frac=mc/len(vals)
    flag=' <-- CONSTANT' if frac>=0.999 else (' <-- near-const' if frac>=0.90 else '')
    print('  %-26s n=%d modal=%s (%.0f%%) uniq=%d%s'%(f,len(vals),str(mode)[:18],100*frac,len(c),flag))

print('\n=== FX-ENRICHED (n=%d): feature vs WIN(pips) and vs R ===' % len(fx_enr))
yb=[r['win'] for r in fx_enr]
R=[r['r_multiple'] for r in fx_enr]
results=[]
for f in NUMERIC:
    x=[num(r.get(f)) for r in fx_enr]
    if all(np.isnan(v) for v in x):
        print('  %-26s all-NaN (skip)'%f); continue
    _,pw,n=perm_num_binary(x,yb)
    _,pr,_=perm_num_cont(x,R)
    if pw is None: continue
    wv=[num(r.get(f)) for r in fx_enr if r['win']==1]; wv=[v for v in wv if not np.isnan(v)]
    lv=[num(r.get(f)) for r in fx_enr if r['win']==0]; lv=[v for v in lv if not np.isnan(v)]
    mw=np.mean(wv) if wv else float('nan'); ml=np.mean(lv) if lv else float('nan')
    results.append((f,'num',pw,pr,n,mw,ml))
for f in CATEG:
    cats=[str(r.get(f)) for r in fx_enr if f in r]
    if not cats: continue
    _,pw,n,labs=perm_cat_binary(cats,[r['win'] for r in fx_enr if f in r])
    if pw is None:
        print('  %-26s single-value (no test)'%f); continue
    # R by category not meaningful for cat; use win perm as primary
    results.append((f,'cat',pw,None,n,None,None))

results.sort(key=lambda z:z[2])
print('%-26s %-4s %-9s %-9s %-4s %-9s %-9s'%('feature','typ','p_win','p_R','n','mean_W','mean_L'))
for f,t,pw,pr,n,mw,ml in results:
    prs = '%.4f'%pr if pr is not None else '   -  '
    mws = '%+.3f'%mw if mw is not None else '  -  '
    mls = '%+.3f'%ml if ml is not None else '  -  '
    print('%-26s %-4s %.4f    %-9s %-4d %-9s %-9s'%(f,t,pw,prs,n,mws,mls))

# Multiple comparison correction across all tested features (win p-values)
pvals=[(f,pw) for f,t,pw,pr,n,mw,ml in results]
m=len(pvals)
print('\n=== MULTIPLE COMPARISON CORRECTION (m=%d tests, win/loss p) ==='%m)
alpha=0.05
bonf=alpha/m
print('Bonferroni threshold = %.5f'%bonf)
srt=sorted(pvals,key=lambda z:z[1])
# Benjamini-Hochberg
print('%-26s p_raw    BH_crit(i/m*a)  Bonf_pass  BH_pass'%'feature')
bh_pass_max=0
for i,(f,p) in enumerate(srt,1):
    crit=i/m*alpha
    if p<=crit: bh_pass_max=i
for i,(f,p) in enumerate(srt,1):
    crit=i/m*alpha
    print('%-26s %.4f   %.5f        %-9s  %s'%(f,p,crit,'YES' if p<bonf else 'no','YES' if i<=bh_pass_max else 'no'))
