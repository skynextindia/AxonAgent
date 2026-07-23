import json, calendar, pickle, bisect, collections

def parse_ts(s):
    if not s: return None
    s=s.replace("T"," ")
    try:
        return calendar.timegm((int(s[0:4]),int(s[5:7]),int(s[8:10]),int(s[11:13]),int(s[14:16]),int(s[17:19]),0,0,0))
    except: return None

with open("D:/AXON.AI/AxonAgent-Agy/scratch/today_ts.pkl","rb") as f:
    d=pickle.load(f)
today_ts=d["today_ts"]; today_prices=d["today_prices"]

# load trades
trades=[]
with open("D:/AXON.AI/AxonAgent-Agy/reports/trade_analytics.jsonl") as f:
    for line in f:
        line=line.strip()
        if not line: continue
        try: t=json.loads(line)
        except: continue
        trades.append(t)

today=[t for t in trades if str(t.get("entry_time","")).startswith("2026-07-20")]
print(f"total trades={len(trades)}  today(entry 07-20)={len(today)}")

full=partial=none=0; details=[]
for t in today:
    sym=t.get("symbol"); e=parse_ts(t.get("entry_time")); x=parse_ts(t.get("exit_time"))
    ts=today_ts.get(sym,[])
    if not ts or e is None or x is None:
        none+=1; details.append((sym,"NONE-nots")); continue
    lo=bisect.bisect_left(ts,e); hi=bisect.bisect_right(ts,x)
    cnt=hi-lo
    span=x-e
    cover_start = ts[0]<=e
    cover_end = ts[-1]>=x
    if cnt>=2 and cover_start and cover_end:
        full+=1; tag="FULL"
    elif cnt>=1:
        partial+=1; tag="PARTIAL"
    else:
        none+=1; tag="NONE"
    details.append((sym,tag,cnt,span))

print(f"JOIN FEASIBILITY today 36: FULL={full} PARTIAL={partial} NONE={none}")
bysym=collections.Counter(t.get("symbol") for t in today)
print("today trades by symbol:", dict(bysym))
tagsym=collections.Counter((sym,tag) for sym,tag,*_ in details)
for k in sorted(tagsym): print("  ",k,tagsym[k])

# ---- gold price hazard: step histogram XAU vs EUR on 07-20 ----
def step_hist(pairs, scale, topn=12):
    # pairs sorted by ts already? ensure sort
    pairs=sorted(pairs)
    steps=collections.Counter()
    prev=None
    for ts,px in pairs:
        if prev is not None:
            dp=round(abs(px-prev)*scale)  # in 'points'
            steps[dp]+=1
        prev=px
    total=sum(steps.values())
    return steps,total

print("\n=== PRICE STEP HISTOGRAM 07-20 (abs consecutive step) ===")
# XAU: pip=0.1? engine 'pips' for gold often 0.1; but grid check use points of 0.01 (cents)
xau=today_prices.get("XAUUSD",[])
eur=today_prices.get("EURUSD",[])
sx,tx=step_hist(xau,100)   # 0.01 units -> integer cents
se,te=step_hist(eur,100000) # 0.00001 units -> integer points
def show(name,s,t,unit):
    print(f"{name}: n_steps={t} unit={unit}")
    for val,c in s.most_common(12):
        print(f"    step={val} ({val*(0.01 if name.startswith('XAU') else 0.00001):.5f})  count={c}  {100*c/t:.1f}%")
    nz=sum(c for v,c in s.items() if v!=0)
    print(f"    zero-step share={100*s.get(0,0)/t:.1f}%  nonzero={nz}")
show("XAUUSD",sx,tx,"0.01")
show("EURUSD",se,te,"0.00001")

# also XAU in 0.05 buckets to expose 5-cent hump
sx5=collections.Counter()
prev=None
for ts,px in sorted(xau):
    if prev is not None:
        sx5[round(abs(px-prev)/0.05)]+=1
    prev=px
t5=sum(sx5.values())
print("\nXAUUSD step in units of 0.05:")
for val,c in sx5.most_common(10):
    print(f"    {val}x0.05 = {val*0.05:.2f}  count={c}  {100*c/t5:.1f}%")
