import json, math
from collections import defaultdict, OrderedDict

PATH = r"D:/AXON.AI/AxonAgent-Agy/reports/trade_analytics.jsonl"

rows = []
malformed = []
with open(PATH, "r", encoding="utf-8") as f:
    for i, line in enumerate(f, 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append((i, json.loads(line)))
        except Exception as e:
            malformed.append((i, str(e)))

n = len(rows)
print("=== BASIC ===")
print("lines parsed:", n, " malformed:", len(malformed))
if malformed:
    for i, e in malformed[:10]:
        print("  malformed line", i, e)

# collect all keys
allkeys = set()
for _, r in rows:
    allkeys.update(r.keys())
print("total distinct keys:", len(allkeys))

def gnum(r, k):
    v = r.get(k, None)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except Exception:
        return None

# ---- W/L, net pips overall ----
wins = losses = flat = 0
net_pips = 0.0
net_r = 0.0
r_present = 0
pip_present = 0
for _, r in rows:
    p = gnum(r, "pips_profit")
    if p is not None:
        pip_present += 1
        net_pips += p
        if p > 0: wins += 1
        elif p < 0: losses += 1
        else: flat += 1
    rm = gnum(r, "r_multiple")
    if rm is not None:
        r_present += 1
        net_r += rm

print("\n=== OVERALL ===")
print(f"pips_profit present: {pip_present}/{n}")
print(f"W/L/flat (by pips sign): {wins}/{losses}/{flat}")
wr = 100.0*wins/(wins+losses) if (wins+losses) else 0
print(f"net_pips: {net_pips:.4f}  win_rate(W/(W+L)): {wr:.2f}%")
print(f"r_multiple present: {r_present}/{n}  net_r: {net_r:.4f}")

# ---- per symbol ----
print("\n=== PER SYMBOL ===")
sym = defaultdict(lambda: {"n":0,"w":0,"l":0,"pips":0.0,"r":0.0,"r_n":0})
for _, r in rows:
    s = r.get("symbol","?")
    d = sym[s]
    d["n"] += 1
    p = gnum(r,"pips_profit")
    if p is not None:
        d["pips"] += p
        if p>0: d["w"]+=1
        elif p<0: d["l"]+=1
    rm = gnum(r,"r_multiple")
    if rm is not None:
        d["r"] += rm; d["r_n"]+=1
for s in sorted(sym, key=lambda x: sym[x]["pips"]):
    d = sym[s]
    wl = d["w"]+d["l"]
    wr = 100.0*d["w"]/wl if wl else 0
    print(f"  {s:8s} n={d['n']:3d}  net_pips={d['pips']:10.4f}  net_R={d['r']:8.4f} (r_n={d['r_n']})  W/L={d['w']}/{d['l']}  wr={wr:.2f}%")

# ---- per day ----
print("\n=== PER DAY (entry_time date) ===")
day = defaultdict(lambda: {"n":0,"w":0,"l":0,"pips":0.0})
for _, r in rows:
    et = r.get("entry_time","") or ""
    dkey = et[:10] if len(et)>=10 else "?"
    d = day[dkey]
    d["n"]+=1
    p = gnum(r,"pips_profit")
    if p is not None:
        d["pips"]+=p
        if p>0: d["w"]+=1
        elif p<0: d["l"]+=1
for dk in sorted(day):
    d=day[dk]; wl=d["w"]+d["l"]; wr=100.0*d["w"]/wl if wl else 0
    print(f"  {dk}  n={d['n']:3d}  net_pips={d['pips']:10.4f}  W/L={d['w']}/{d['l']}  wr={wr:.2f}%")

# show a sample entry_time format
print("\nsample entry_time values:")
for _, r in rows[:3]:
    print("   ", repr(r.get("entry_time")), "->", repr(r.get("exit_time")))

# ---- field coverage ----
print("\n=== FIELD COVERAGE (non-null / non-empty) ===")
keyfields = ["symbol","direction","entry_time","exit_time","entry_price","exit_price",
    "initial_sl","initial_tp","initial_sl_pips","regime","regime_confidence","mtf_alignment",
    "mtf_context","mtf_h4_bias","mtf_h1_bias","mtf_m15_bias","anomaly_velocity_z","vel_pct",
    "vel_tick_eff","vel_vol_pips","vel_decay_ratio","vel_is_unusual","displacement_classification",
    "displacement_ratio","net_displacement_pips","reversal_pressure","at_structure",
    "distance_to_sr","room_available","nearest_level_type","active_sweeps","active_breaks",
    "liquidity_void","signal_quality","exit_reason","exit_gate","exit_price","pips_profit",
    "r_multiple","max_favorable_excursion","max_adverse_excursion","time_in_drawdown_sec",
    "health_score_at_exit","ticks_in_trade","exit_vel_pct","exit_displacement","exit_phase","exit_thesis"]
def present(v):
    return not (v is None or v == "" )
for k in keyfields:
    cnt = 0
    inkey = 0
    for _, r in rows:
        if k in r:
            inkey += 1
            if present(r.get(k)):
                cnt += 1
    flag = "" if cnt==n else "  <-- partial" if cnt>0 else "  <-- ALL NULL/MISSING"
    print(f"  {k:28s} present={cnt:3d}/{n}  (key-exists={inkey}){flag}")

# also report any keyfields entirely absent from schema
missing_schema = [k for k in keyfields if k not in allkeys]
print("\nkeyfields NOT in any row's schema:", missing_schema)

# ---- duplicate tickets ----
print("\n=== DUPLICATES ===")
# find ticket-like fields
ticketkeys = [k for k in allkeys if "ticket" in k.lower() or k.lower() in ("id","trade_id","order_id","position_id","deal")]
print("candidate ticket keys:", ticketkeys)
for tk in ticketkeys:
    seen = defaultdict(list)
    for ln, r in rows:
        v = r.get(tk)
        if present(v):
            seen[str(v)].append(ln)
    dups = {k:v for k,v in seen.items() if len(v)>1}
    print(f"  field {tk}: {len(dups)} duplicated values")
    for k,v in list(dups.items())[:10]:
        print(f"     {tk}={k} on lines {v}")

# duplicate by (symbol, entry_time, direction)
seen = defaultdict(list)
for ln, r in rows:
    key = (r.get("symbol"), r.get("entry_time"), r.get("direction"), r.get("entry_price"))
    seen[key].append(ln)
dups = {k:v for k,v in seen.items() if len(v)>1}
print(f"  dup by (symbol,entry_time,direction,entry_price): {len(dups)}")
for k,v in list(dups.items())[:10]:
    print("     ", k, "lines", v)

# ---- sign consistency ----
print("\n=== PIPS SIGN vs PRICE-DERIVED ===")
mismatch = []
checked = 0
for ln, r in rows:
    d = (r.get("direction") or "").lower()
    ep = gnum(r,"entry_price"); xp = gnum(r,"exit_price"); pp = gnum(r,"pips_profit")
    if ep is None or xp is None or pp is None:
        continue
    if d in ("buy","long","b"):
        raw = xp - ep
    elif d in ("sell","short","s"):
        raw = ep - xp
    else:
        continue
    checked += 1
    # sign compare (ignore near-zero)
    if abs(pp) < 1e-9 or abs(raw) < 1e-12:
        continue
    if (raw > 0) != (pp > 0):
        mismatch.append((ln, r.get("symbol"), d, ep, xp, pp, raw))
print(f"checked {checked} rows with dir+prices+pips")
print(f"sign mismatches: {len(mismatch)}")
for m in mismatch[:30]:
    print("   line", m[0], m[1], m[2], "entry",m[3],"exit",m[4],"pips",m[5],"raw",m[6])

# direction value distribution
dirvals = defaultdict(int)
for _,r in rows:
    dirvals[r.get("direction")]+=1
print("\ndirection values:", dict(dirvals))
# exit_gate distribution
print("\nexit_gate values:")
eg = defaultdict(lambda:[0,0.0])
for _,r in rows:
    g = r.get("exit_gate")
    p = gnum(r,"pips_profit") or 0
    eg[g][0]+=1; eg[g][1]+=p
for g,(c,pp) in sorted(eg.items(), key=lambda x:x[1][1]):
    print(f"  {str(g):28s} n={c:3d}  net_pips={pp:10.4f}")
print("\nexit_reason values:")
er = defaultdict(lambda:[0,0.0])
for _,r in rows:
    g = r.get("exit_reason")
    p = gnum(r,"pips_profit") or 0
    er[g][0]+=1; er[g][1]+=p
for g,(c,pp) in sorted(er.items(), key=lambda x:x[1][1]):
    print(f"  {str(g):40s} n={c:3d}  net_pips={pp:10.4f}")
