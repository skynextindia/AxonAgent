"""Pure-python separation statistics (no numpy/scipy dependency).

rank-AUC = P(a random positive ranks above a random negative). 0.5 = no
separation; distance from 0.5 = discriminating power (either direction). A
deterministic bootstrap (seeded by index, since Math.random is unavailable and
determinism is required) gives a rough CI so we never over-read a small-n AUC.
"""

from __future__ import annotations

import statistics as st
from typing import Any, Dict, List, Optional, Sequence


def _vals(rows, attr) -> List[float]:
    out = []
    for r in rows:
        v = getattr(r, attr, None)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(float(v))
    return out


def summarize(rows, attr) -> Dict[str, Any]:
    xs = _vals(rows, attr)
    if not xs:
        return {"n": 0}
    xs_sorted = sorted(xs)
    return {
        "n": len(xs),
        "median": round(st.median(xs), 5),
        "mean": round(st.mean(xs), 5),
        "p25": round(xs_sorted[len(xs_sorted) // 4], 5),
        "p75": round(xs_sorted[(3 * len(xs_sorted)) // 4], 5),
        "min": round(min(xs), 5),
        "max": round(max(xs), 5),
    }


def rank_auc(pos: Sequence, neg: Sequence, attr: str) -> Optional[float]:
    P = _vals(pos, attr)
    N = _vals(neg, attr)
    if len(P) < 3 or len(N) < 3:
        return None
    wins = 0.0
    for p in P:
        for n in N:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(P) * len(N))


def _lcg(seed: int):
    """Deterministic pseudo-random stream (Math.random is unavailable and would
    break reproducibility). Park-Miller minimal standard generator."""
    state = (seed % 2147483646) + 1
    while True:
        state = (state * 48271) % 2147483647
        yield state / 2147483647.0


def auc_bootstrap_ci(pos: Sequence, neg: Sequence, attr: str,
                     iters: int = 500, seed: int = 12345):
    """Deterministic bootstrap CI for the AUC. Returns (auc, lo, hi) or None."""
    P = _vals(pos, attr)
    N = _vals(neg, attr)
    if len(P) < 5 or len(N) < 5:
        return None
    base = rank_auc(pos, neg, attr)
    rng = _lcg(seed + len(P) * 131 + len(N))
    aucs = []
    for _ in range(iters):
        rp = [P[int(next(rng) * len(P)) % len(P)] for _ in P]
        rn = [N[int(next(rng) * len(N)) % len(N)] for _ in N]
        wins = 0.0
        for p in rp:
            for n in rn:
                wins += 1.0 if p > n else 0.5 if p == n else 0.0
        aucs.append(wins / (len(rp) * len(rn)))
    aucs.sort()
    lo = aucs[int(0.025 * len(aucs))]
    hi = aucs[int(0.975 * len(aucs)) - 1]
    return round(base, 3), round(lo, 3), round(hi, 3)


def feature_ranking(pos: Sequence, neg: Sequence, attrs: List[str]) -> List[Dict[str, Any]]:
    """Rank features by |AUC - 0.5| (discriminating power, either sign)."""
    rows = []
    for a in attrs:
        auc = rank_auc(pos, neg, a)
        ci = auc_bootstrap_ci(pos, neg, a)
        rows.append({
            "feature": a,
            "auc": None if auc is None else round(auc, 3),
            "power": None if auc is None else round(abs(auc - 0.5), 3),
            "ci": (ci[1], ci[2]) if ci else None,
            "ci_excludes_0.5": (bool(ci and (ci[1] > 0.5 or ci[2] < 0.5))),
            "pos_summary": summarize(pos, a),
            "neg_summary": summarize(neg, a),
        })
    rows.sort(key=lambda r: (-(r["power"] or -1)))
    return rows


def categorical_split(pos: Sequence, neg: Sequence, attr: str) -> Dict[str, Any]:
    import collections
    pc = collections.Counter(getattr(r, attr, None) for r in pos)
    nc = collections.Counter(getattr(r, attr, None) for r in neg)
    keys = set(pc) | set(nc)
    return {str(k): {"pos": pc.get(k, 0), "neg": nc.get(k, 0)} for k in keys}
