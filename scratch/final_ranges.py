import csv, glob, os, calendar, time, json
from collections import Counter, defaultdict

SCH = {41: 41, 39: 39, 33: 33, 25: 25}
IDX = {  # (width) -> (i_tickrate60 or None, i_volpips)
    41: (9, 6), 39: (9, 6), 33: (9, 6), 25: (None, 6),
}
def pipsz(s): return 0.01 if ("JPY" in s or "XAU" in s) else 0.0001
def parse(s):
    b,_,f = s.strip().partition(".")
    try: e = calendar.timegm(time.strptime(b, "%Y-%m-%d %H:%M:%S"))
    except Exception: return None
    return e + (float("0."+f) if f else 0.0)
def fmt(t): return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t)) if t is not None else None

SYNTH_START = calendar.timegm(time.strptime("2026-07-18 00:00:00", "%Y-%m-%d %H:%M:%S"))

files = defaultdict(list)
for p in sorted(glob.glob("D:/AXON.AI/AxonAgent-Agy/reports/engine_snapshots_*.csv")):
    files[os.path.basename(p)[len("engine_snapshots_"):-len(".csv")].split("_")[0]].append(p.replace("\\","/"))

res = {}
for sym in sorted(files):
    P = pipsz(sym)
    merged = {}
    dup = 0
    synth_first = synth_last = None
    synth_rows = 0
    weekend_rows_pre = 0     # weekend rows BEFORE the 07-18 block (should be 0)
    for path in files[sym]:
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            rd = csv.reader(fh); next(rd, None)
            for r in rd:
                w = len(r)
                if w not in SCH: continue
                ts = parse(r[0])
                if ts is None: continue
                try: px = float(r[1])
                except Exception: continue
                sec = int(ts)
                if sec in merged: dup += 1
                else: merged[sec] = px
                if ts >= SYNTH_START:
                    synth_rows += 1
                    if synth_first is None or ts < synth_first: synth_first = ts
                    if synth_last is None or ts > synth_last: synth_last = ts
                elif time.gmtime(ts).tm_wday >= 5:
                    weekend_rows_pre += 1
    allsec = sorted(merged)
    clean = [e for e in allsec if e < SYNTH_START]
    def bars(xs): return len(set((e // 900) * 900 for e in xs))
    # flat-tick ratio + step profile for clean vs synth (discriminator evidence)
    def profile(xs):
        flat = 0; n = 0; small = 0
        for i in range(1, len(xs)):
            d = abs(merged[xs[i]] - merged[xs[i-1]]) / P
            n += 1
            if d < 0.05: flat += 1
            if d <= 0.15: small += 1
        return dict(n=n, flat_pct=round(100.0*flat/max(1,n),1), le_0p1_pct=round(100.0*small/max(1,n),1))
    synth = [e for e in allsec if e >= SYNTH_START]
    res[sym] = dict(
        total_second_epochs=len(allsec), duplicate_second_collisions=dup,
        full_first=fmt(allsec[0]), full_last=fmt(allsec[-1]), full_m15_bars=bars(allsec),
        clean_first=fmt(clean[0]), clean_last=fmt(clean[-1]),
        clean_second_epochs=len(clean), clean_m15_bars=bars(clean),
        clean_span_days=round((clean[-1]-clean[0])/86400.0, 2),
        synthetic_second_epochs=len(synth), synthetic_m15_bars=bars(synth),
        synthetic_raw_rows=synth_rows,
        synthetic_first=fmt(synth_first), synthetic_last=fmt(synth_last),
        weekend_rows_before_0718=weekend_rows_pre,
        profile_clean=profile(clean), profile_synth=profile(synth),
    )
print(json.dumps(res, indent=1))
with open("D:/AXON.AI/AxonAgent-Agy/scratch/final_ranges.json","w") as fh:
    json.dump(res, fh, indent=1)
