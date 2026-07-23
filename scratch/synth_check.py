import csv, calendar, time, json
from collections import Counter, defaultdict

S41=["timestamp","price","vel_pct","vel_z","vel_decaying","decay_ratio","vol_pips","tick_eff",
"tick_rate_10s","tick_rate_60s","tick_rate_300s","displacement_velocity","abs_velocity","velocity_ratio",
"is_unusual","is_accelerating","disp_class","disp_ratio","net_disp_pips","regime","h4_bias","h1_bias",
"m15_bias","reversal_pressure","is_exhaustion_zone","structure_break","active_sweeps","active_breaks",
"liquidity_void","entry_state","entry_dir","signal_quality","skip_reason","at_structure","dist_to_sr",
"dist_to_liq","room_pips","near_level_type","near_level_price","range_pos","range_used"]

for d in ("2026-07-09","2026-07-10","2026-07-11","2026-07-12","2026-07-13","2026-07-14",
          "2026-07-15","2026-07-16","2026-07-17","2026-07-18","2026-07-19"):
    t = time.strptime(d, "%Y-%m-%d")
    print(d, ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][t.tm_wday])

def parse(s):
    b,_,f = s.strip().partition(".")
    e = calendar.timegm(time.strptime(b, "%Y-%m-%d %H:%M:%S"))
    return e + (float("0."+f) if f else 0.0)

for sym, pipsz in (("EURUSD",0.0001), ("XAUUSD",0.01)):
    path = "D:/AXON.AI/AxonAgent-Agy/reports/engine_snapshots_%s.csv" % sym
    perday = defaultdict(lambda: dict(n=0, dts=Counter(), steps=Counter(), up=0, dn=0, flat=0,
                                      tr60=Counter(), volp=Counter(), prev=None, prevts=None,
                                      firstpx=None, lastpx=None))
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        rd = csv.reader(fh); next(rd)
        for r in rd:
            if len(r) != 41: continue
            ts = parse(r[0]); px = float(r[1])
            day = time.strftime("%Y-%m-%d", time.gmtime(ts))
            a = perday[day]; a["n"] += 1
            if a["firstpx"] is None: a["firstpx"] = px
            a["lastpx"] = px
            a["tr60"][r[9]] += 1
            a["volp"][r[6]] += 1
            if a["prev"] is not None:
                dt = round(ts - a["prevts"], 3)
                a["dts"][dt] += 1
                mv = round((px - a["prev"]) / pipsz, 1)
                a["steps"][abs(mv)] += 1
                if mv > 0: a["up"] += 1
                elif mv < 0: a["dn"] += 1
                else: a["flat"] += 1
            a["prev"] = px; a["prevts"] = ts
    print("\n#####", sym)
    for day in sorted(perday):
        a = perday[day]
        wd = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][time.strptime(day,"%Y-%m-%d").tm_wday]
        tot = a["up"]+a["dn"]+a["flat"]
        print(" %s %s n=%d up/dn/flat=%d/%d/%d (up%%=%.1f) px %s->%s" % (
            day, wd, a["n"], a["up"], a["dn"], a["flat"],
            100.0*a["up"]/max(1,a["up"]+a["dn"]), a["firstpx"], a["lastpx"]))
        print("    dt_top:", dict(a["dts"].most_common(6)))
        print("    |step|pips_top:", dict(a["steps"].most_common(6)))
        print("    tick_rate_60s_top:", dict(a["tr60"].most_common(4)))
        print("    vol_pips_top:", dict(a["volp"].most_common(4)))
