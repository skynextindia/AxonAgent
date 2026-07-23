import csv, calendar, collections, statistics, json, os

REPORTS = "D:/AXON.AI/AxonAgent-Agy/reports"
SYMBOLS = ["XAUUSD","EURUSD","GBPUSD","AUDUSD","USDJPY"]

def parse_ts(s):
    # "%Y-%m-%d %H:%M:%S" UTC
    try:
        st = calendar.timegm((int(s[0:4]),int(s[5:7]),int(s[8:10]),int(s[11:13]),int(s[14:16]),int(s[17:19]),0,0,0))
        return st
    except Exception:
        return None

# ---- per symbol: rows per day + collect 07-20 ts and prices ----
day_counts = {}   # sym -> {date: count}
today_ts = {}     # sym -> sorted list of epoch seconds for 07-20
today_prices = {} # sym -> list of (ts, price) for 07-20 (only XAU, EUR needed but keep all small)

for sym in SYMBOLS:
    path = f"{REPORTS}/engine_snapshots_{sym}.csv"
    dc = collections.Counter()
    ts_list = []
    px_list = []
    with open(path, "r", newline="") as f:
        r = csv.reader(f)
        header = next(r)
        pi = header.index("price")
        for row in r:
            if not row: continue
            t = row[0]
            date = t[0:10]
            dc[date]+=1
            if date == "2026-07-20":
                e = parse_ts(t)
                if e is not None:
                    ts_list.append(e)
                    if sym in ("XAUUSD","EURUSD"):
                        try: px_list.append((e, float(row[pi])))
                        except: pass
    day_counts[sym]=dc
    ts_list.sort()
    today_ts[sym]=ts_list
    today_prices[sym]=px_list

print("=== ROWS PER DAY PER SYMBOL ===")
for sym in SYMBOLS:
    dc = day_counts[sym]
    parts = []
    for d in sorted(dc):
        tag = " [SYNTH]" if d=="2026-07-18" else ""
        parts.append(f"{d}={dc[d]}{tag}")
    print(sym, "|", "  ".join(parts))

print("\n=== TODAY 07-20 COVERAGE PER SYMBOL ===")
for sym in SYMBOLS:
    ts = today_ts[sym]
    if not ts:
        print(sym, "no 07-20 rows"); continue
    gaps = [ts[i+1]-ts[i] for i in range(len(ts)-1)]
    med = statistics.median(gaps) if gaps else 0
    big = [(ts[i],ts[i+1],ts[i+1]-ts[i]) for i in range(len(ts)-1) if ts[i+1]-ts[i]>300]
    import time
    f0=time.strftime("%H:%M:%S",time.gmtime(ts[0]))
    f1=time.strftime("%H:%M:%S",time.gmtime(ts[-1]))
    print(f"{sym}: n={len(ts)} first={f0} last={f1} med_gap={med:.1f}s gaps>300s={len(big)}")
    for g in big[:6]:
        print("     gap", time.strftime("%H:%M:%S",time.gmtime(g[0])),"->",time.strftime("%H:%M:%S",time.gmtime(g[1])), f"{g[2]}s")

# save for join step
import pickle
with open("D:/AXON.AI/AxonAgent-Agy/scratch/today_ts.pkl","wb") as f:
    pickle.dump({"today_ts":today_ts,"today_prices":today_prices}, f)
print("\nsaved pkl")
