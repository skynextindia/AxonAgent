import csv, glob, os
from collections import Counter
for p in sorted(glob.glob("D:/AXON.AI/AxonAgent-Agy/reports/engine_snapshots_*.csv")):
    c = Counter()
    with open(p, "r", newline="", encoding="utf-8", errors="replace") as fh:
        rd = csv.reader(fh)
        hdr = next(rd)
        for r in rd:
            if r:
                c[len(r)] += 1
    print(os.path.basename(p), "header=", len(hdr), "rowwidths=", dict(c))
