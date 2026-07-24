"""Does the confluence score predict outcome? Run AFTER a restart onto the
instrumented engine, once trades carry confluence_score (strategy_version !=
"<none>"). Read-only; never trades.

Companion to the per-trade confluence_score instrumentation (reversal_model ~530,
trade_analytics.record_entry). The score distribution work showed most triggers
score ~0.40 against a 0.45-0.60 floor -- a real gap, not a rounding miss. The open
question that gap raises: among trades that DID pass the floor, does a higher score
actually predict a better outcome?

If win% and avg pips rise with the score bucket -> the floor is a meaningful quality
filter and could even move up. If they are flat across buckets -> the score is noise
and the floor is an arbitrary trade-count throttle, so the low trade count is a
scoring problem, not a threshold problem.

Usage:
    python -m axonai.scripts.score_vs_outcome
"""
import json
from collections import defaultdict

LOG = r"D:/AXON.AI/AxonAgent-Agy/reports/trade_analytics.jsonl"


def load(path=LOG):
    out = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def num(r, k, d=0.0):
    try:
        return float(r.get(k, d))
    except (TypeError, ValueError):
        return d


def spearman(xs, ys):
    """Rank correlation with average-rank tie handling. No scipy dependency."""
    n = len(xs)
    if n < 2:
        return 0.0

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        rk = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                rk[order[k]] = avg
            i = j + 1
        return rk

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    numer = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den = (sum((rx[i] - mx) ** 2 for i in range(n))
           * sum((ry[i] - my) ** 2 for i in range(n))) ** 0.5
    return numer / den if den else 0.0


def main():
    rows = load()
    fx = [r for r in rows
          if "XAU" not in (r.get("symbol") or "").upper()
          and (r.get("exit_reason") or r.get("exit_time"))
          and num(r, "confluence_score") > 0.0]

    inst = [r for r in rows if (r.get("strategy_version") or "<none>") != "<none>"]
    print(f"instrumented closed FX trades with confluence_score: {len(fx)}")
    print(f"(total records with a strategy_version stamp: {len(inst)})")
    if len(fx) < 20:
        print("\nNot enough instrumented trades yet. Need ~50+ for a hint, 200+ to trust.")
        return

    buckets = [(0.0, 0.55), (0.55, 0.65), (0.65, 0.75), (0.75, 0.85), (0.85, 1.01)]
    by = defaultdict(list)
    for r in fx:
        s = num(r, "confluence_score")
        for lo, hi in buckets:
            if lo <= s < hi:
                by[(lo, hi)].append(r)
                break

    print(f"\n{'score band':<14}{'n':>5}{'win%':>8}{'avg_pips':>10}{'tot_pips':>10}")
    for lo, hi in buckets:
        g = by[(lo, hi)]
        if not g:
            print(f"{lo:.2f}-{hi:.2f}      (none)")
            continue
        pips = [num(r, "pips_profit") for r in g]
        wins = sum(1 for p in pips if p > 0)
        print(f"{lo:.2f}-{hi:.2f}    {len(g):>5}{100.0 * wins / len(g):>7.1f}%"
              f"{sum(pips) / len(g):>+10.2f}{sum(pips):>+10.1f}")

    xs = [num(r, "confluence_score") for r in fx]
    ys = [num(r, "pips_profit") for r in fx]
    rho = spearman(xs, ys)
    print(f"\nSpearman(confluence_score, pips_profit) = {rho:+.3f}  (n={len(fx)})")
    print("interpretation: |rho| < 0.1 => score is noise, floor is arbitrary;")
    print("                rho > 0.2 with rising buckets => floor is a real quality filter.")


if __name__ == "__main__":
    main()
