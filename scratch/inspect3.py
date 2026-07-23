import json
from collections import Counter, defaultdict
PATH = r"D:/AXON.AI/AxonAgent-Agy/reports/trade_analytics.jsonl"
rows=[]
with open(PATH,encoding="utf-8") as f:
    for i,l in enumerate(f,1):
        l=l.strip()
        if l: rows.append((i,json.loads(l)))

# flat trade
print("=== FLAT (pips==0) rows ===")
for ln,r in rows:
    if float(r["pips_profit"])==0:
        print(f"  line {ln} {r['symbol']} {r['direction']} entry={r['entry_price']} exit={r['exit_price']} MFE={r.get('max_favorable_excursion')} reason={r.get('exit_reason')}")

# corrupt time_in_drawdown
print("\n=== time_in_drawdown_sec anomalies (<0 or huge) ===")
for ln,r in rows:
    v=r.get("time_in_drawdown_sec")
    if v is not None and float(v)<0:
        print(f"  line {ln} {r['symbol']} tdd={v} entry_time={r['entry_time']} exit_time={r['exit_time']}")

# gold price grid: fractional cents of entry+exit prices
print("\n=== GOLD price granularity (last-2-decimals of entry/exit) ===")
gold_frac=Counter()
fx_frac=Counter()
for ln,r in rows:
    for pk in ("entry_price","exit_price","initial_sl","initial_tp"):
        v=r.get(pk)
        if v is None: continue
        if r["symbol"]=="XAUUSD":
            cents=round((float(v)*100)%100)  # hundredths
            gold_frac[cents%5]+=1  # remainder mod 5 to test 0.05 clustering
# test: for gold, how many prices land on .x0 or .x5 (multiple of 0.05)?
g_on05=g_tot=0
g_last_digit=Counter()
for ln,r in rows:
    if r["symbol"]!="XAUUSD": continue
    for pk in ("entry_price","exit_price"):
        v=r.get(pk)
        if v is None: continue
        hundredths=round(float(v)*100)
        g_last_digit[hundredths%10]+=1
        g_tot+=1
        if hundredths%5==0: g_on05+=1
print(f"  gold entry/exit prices on 0.05 grid: {g_on05}/{g_tot} ({100*g_on05/g_tot:.1f}%)")
print(f"  gold last-hundredths-digit distribution: {dict(sorted(g_last_digit.items()))}")

# same for a FX pair (EURUSD) last digit of 5th decimal
fx_last=Counter(); fx_tot=0
for ln,r in rows:
    if r["symbol"]!="EURUSD": continue
    for pk in ("entry_price","exit_price"):
        v=r.get(pk)
        if v is None: continue
        d5=round(float(v)*100000)%10
        fx_last[d5]+=1; fx_tot+=1
print(f"  EURUSD 5th-decimal digit distribution (n={fx_tot}): {dict(sorted(fx_last.items()))}")

# net R for enriched only
rs=[float(r["r_multiple"]) for ln,r in rows if r.get("r_multiple") not in (None,"")]
print(f"\n=== R-multiple: n={len(rs)} sum={sum(rs):.4f} min={min(rs)} max={max(rs)} ===")

# per-symbol R among enriched
symR=defaultdict(list)
for ln,r in rows:
    if r.get("r_multiple") not in (None,""):
        symR[r["symbol"]].append(float(r["r_multiple"]))
for s,v in symR.items():
    print(f"   {s}: n={len(v)} sumR={sum(v):.3f}")
