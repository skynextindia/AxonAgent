"""Re-entry distance — pure logic (no MT5), so it is unit-testable.

Question: when the engine opens a new trade, how far — in pips and minutes — is it
from where it last CLOSED a trade of the SAME symbol + SAME direction? The live engine
has no such check (only a time cooldown), so a fade can re-open a SELL a pip or two from
where it just exited a SELL. This module measures that distance across a trade history and
buckets net P&L by "was this a near re-entry?" — read-only, decides nothing.
"""
from __future__ import annotations
from typing import Optional


def pip_size(symbol: str) -> float:
    return 0.01 if ("JPY" in symbol.upper() or "XAU" in symbol.upper()) else 0.0001


def annotate_reentries(positions: list) -> list:
    """Given closed positions, stamp each with distance/gap from the prior same-(symbol,dir)
    EXIT. `positions`: list of dicts with keys sym, dir, entry_t, entry_px, exit_t, exit_px,
    net (entry_t/exit_t = epoch seconds). Returns a NEW list, entry-time-ordered, each with:
      dist_pips  — |entry_px - prior_exit_px| / pip   (None if no prior same-dir trade)
      gap_min    — (entry_t - prior_exit_t) / 60      (None if no prior)
    Only prior positions whose EXIT precedes this entry are eligible (no look-ahead)."""
    ordered = sorted(positions, key=lambda p: p["entry_t"])
    last_exit: dict[tuple, dict] = {}   # (sym,dir) -> most recent exited position
    out = []
    for p in ordered:
        key = (p["sym"], p["dir"])
        prior = last_exit.get(key)
        dist_pips = gap_min = None
        if prior is not None and prior["exit_t"] <= p["entry_t"]:
            pip = pip_size(p["sym"])
            dist_pips = round(abs(p["entry_px"] - prior["exit_px"]) / pip, 1)
            gap_min = round((p["entry_t"] - prior["exit_t"]) / 60.0, 1)
        q = dict(p, dist_pips=dist_pips, gap_min=gap_min)
        out.append(q)
        # this position becomes the new "last exit" for its key once it has closed
        if p.get("exit_t") is not None:
            if prior is None or p["exit_t"] >= prior["exit_t"]:
                last_exit[key] = p
    return out


def is_near_reentry(row: dict, max_pips: float, max_min: float) -> bool:
    """A near re-entry = re-opened within max_pips AND max_min of a same-dir exit."""
    dp, gm = row.get("dist_pips"), row.get("gap_min")
    return dp is not None and gm is not None and dp <= max_pips and gm <= max_min


def summarize(annotated: list, max_pips: float = 5.0, max_min: float = 60.0) -> dict:
    """Split into near re-entries vs the rest; return counts + net for each bucket."""
    near = [r for r in annotated if is_near_reentry(r, max_pips, max_min)]
    rest = [r for r in annotated if not is_near_reentry(r, max_pips, max_min)]

    def agg(rows):
        nets = [float(r["net"]) for r in rows if r.get("net") is not None]
        wins = sum(1 for n in nets if n > 0)
        return {"n": len(rows), "net": round(sum(nets), 2),
                "avg": round(sum(nets) / len(nets), 2) if nets else 0.0,
                "win_pct": round(100 * wins / len(nets), 1) if nets else 0.0}

    return {"max_pips": max_pips, "max_min": max_min,
            "near": agg(near), "rest": agg(rest),
            "near_rows": near}
