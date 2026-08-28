"""Orchestrator (READ-ONLY):

    python -m research.exit_cut_forensics.run_cut_forensics

Answers the two questions the user asked, before anything touches live config:
  (a) How much of total LEAD loss is the full-stop breakout-fade SELL tail?
      -> full history, from exit_reason (no MFE needed).
  (b) What would a "faded level broke -> cut" exit rule have netted?
      -> buffer sweep on the MFE/MAE subset (tail saved MINUS break-then-revert
         cost), reported as a naive lower bound AND an optimistic upper bound.

Writes EXIT_CUT_FORENSIC.md + cut_summary.json under ./out/. Nothing else.
"""

from __future__ import annotations

import json
import os
from typing import List

from research.direction_location_forensics.loader import load_all, parse_ts, ForensicTrade
from . import cut_sim

_THIS = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(_THIS, "out")
BUFFERS = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0]


def _assert_out(path: str) -> None:
    rp = os.path.abspath(path)
    if os.path.abspath(OUT) not in rp:
        raise RuntimeError(f"refusing to write outside out/: {rp}")


def _mfe_subset(lead: List[ForensicTrade]) -> List[ForensicTrade]:
    return [t for t in lead if t.mae_pips is not None]


def _span(trades: List[ForensicTrade]):
    ds = sorted(d.date().isoformat() for d in
                (parse_ts(t) for t in trades) if d)
    return (ds[0], ds[-1], len(ds)) if ds else ("n/a", "n/a", 0)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    lead = load_all()["lead"]

    tail = cut_sim.tail_share(lead)
    sub = _mfe_subset(lead)
    lo, hi, n = _span(sub)
    sweep = cut_sim.buffer_sweep(sub, BUFFERS)
    best = max(sweep, key=lambda s: s["net_delta_naive"])

    # Per-trade detail at the best buffer (auditable).
    detail = [r for r in cut_sim.simulate_all(sub, best["buffer"]) if r.fired]
    detail.sort(key=lambda r: r.delta)

    summary = {"tail_share": tail, "mfe_subset": {"n": len(sub), "span": [lo, hi]},
               "buffer_sweep": sweep, "best_buffer": best}
    sp = os.path.join(OUT, "cut_summary.json")
    _assert_out(sp)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)

    _write_md(tail, sub, (lo, hi), sweep, best, detail)

    # console
    print("=== EXIT-CUT FORENSIC (lead, read-only) ===")
    print(f"(a) TAIL: total lead loss {tail['total_loss_pips']}p; "
          f"full-stop SELL losers = {tail['full_stop_sell_loss_pips']}p "
          f"({int((tail['full_stop_sell_share'] or 0)*100)}% of all loss), "
          f"n={tail['full_stop_sell_n']}")
    print(f"(b) CUT SIM on MFE subset n={len(sub)} span {lo}..{hi}:")
    print(f"    {'buf':>4s} {'fired':>5s} {'save':>4s} {'cost':>5s} {'net(naive)':>10s} {'net(opt)':>9s}")
    for s in sweep:
        mark = "  <= best" if s is best else ""
        print(f"    {s['buffer']:4.0f} {s['n_fired']:5d} {s['n_savers']:4d} "
              f"{s['n_casualties']:5d} {s['net_delta_naive']:10.1f} "
              f"{s['net_delta_optimistic']:9.1f}{mark}")
    print(f"\nBEST buffer={best['buffer']:.0f}p: naive net {best['net_delta_naive']:+.1f}p "
          f"(saved {best['pips_saved']:+.1f} on {best['n_savers']} losers, "
          f"cost {best['pips_cost']:+.1f} on {best['n_casualties']} reverts); "
          f"optimistic ceiling {best['net_delta_optimistic']:+.1f}p")
    print("reports: out/EXIT_CUT_FORENSIC.md, out/cut_summary.json")
    return 0


def _write_md(tail, sub, span, sweep, best, detail) -> None:
    lo, hi = span
    L = []
    L.append("# EXIT-CUT FORENSIC — would a \"level broke → cut\" rule flip the lead?\n")
    L.append("_Read-only measurement. No production file, config, or order touched. "
             "Consumes the reconstructed lead trades only._\n")
    L.append("## (a) The tail — full history, from exit_reason\n")
    L.append(f"- Total lead loss: **{tail['total_loss_pips']} p**")
    L.append(f"- Full-stop losers (all): {tail['full_stop_all_loss_pips']} p "
             f"across {tail['full_stop_all_n']} trades")
    L.append(f"- **Full-stop SELL losers: {tail['full_stop_sell_loss_pips']} p "
             f"= {int((tail['full_stop_sell_share'] or 0)*100)}% of ALL lead loss** "
             f"({tail['full_stop_sell_n']} trades)")
    L.append("\nThe breakout-fade SELL that rides to the full stop is the single "
             "largest loss source. That is the tail the cut rule targets.\n")
    L.append("## (b) The cut simulation — MFE/MAE subset\n")
    L.append(f"Subset with MAE + level data: **n={len(sub)}**, span {lo} … {hi} "
             "(single adverse regime — see caveats).\n")
    L.append("Rule: a fade expects its S/R level to hold; if adverse excursion "
             "breaks that level by `buffer`, exit at `-(level_dist+buffer)` instead "
             "of the full stop. Two bounds because MFE/MAE give magnitude, not order:\n")
    L.append("- **naive** = cut fires on the first break regardless of final "
             "outcome (realistic for a rule that can't see the future) — *lower bound*.")
    L.append("- **optimistic** = cut only the trades that actually ended as losses "
             "(a perfect break-detector) — *upper bound*.\n")
    L.append("| buffer | fired | savers | casualties | pips saved | pips cost | **net (naive)** | net (optimistic) |")
    L.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for s in sweep:
        star = " ⭐" if s is best else ""
        L.append(f"| {s['buffer']:.0f}p{star} | {s['n_fired']} | {s['n_savers']} | "
                 f"{s['n_casualties']} | {s['pips_saved']:+.1f} | {s['pips_cost']:+.1f} | "
                 f"**{s['net_delta_naive']:+.1f}** | {s['net_delta_optimistic']:+.1f} |")
    L.append(f"\n**Best buffer = {best['buffer']:.0f}p** → naive net "
             f"**{best['net_delta_naive']:+.1f} p** (rescues {best['pips_saved']:+.1f}p on "
             f"{best['n_savers']} losers, costs {best['pips_cost']:+.1f}p on "
             f"{best['n_casualties']} break-then-revert winners); optimistic ceiling "
             f"{best['net_delta_optimistic']:+.1f}p.\n")
    L.append(f"### Per-trade at buffer={best['buffer']:.0f}p (fired only, worst delta first)\n")
    L.append("| dir | sym | actual | cut→ | delta | kind | lvlDist | MAE |")
    L.append("|---|---|---:|---:|---:|---|---:|---:|")
    for r in detail:
        L.append(f"| {r.direction} | {r.symbol} | {r.actual_pips:+.1f} | "
                 f"{(r.cut_pips or 0):+.1f} | {r.delta:+.1f} | {r.kind} | "
                 f"{(r.level_dist if r.level_dist is not None else 0):.1f} | "
                 f"{(r.mae or 0):.1f} |")
    L.append("\n## Caveats (why this is measurement, not a green light)\n")
    L.append("1. **Order unknown.** MFE/MAE are magnitudes; we can't prove a break "
             "preceded a profit. The naive net is the honest realistic figure; the "
             "true value sits between naive and optimistic.")
    L.append("2. **One regime.** The MFE/MAE subset is a single adverse ~10-day "
             "window. A buffer tuned here is in-sample — it is a size estimate, not "
             "a validated parameter.")
    L.append("3. **Slippage/fill** at the break is modeled as exactly "
             "`level_dist+buffer`; a real cut fills slightly worse.")
    L.append("4. Applies only where the faded level is on the ADVERSE side "
             "(true breakout-fade geometry); sell-into-support trades are excluded "
             "by construction.\n")
    L.append("_Next gate before any arming: log MFE/MAE + a coarse adverse-path "
             "timestamp on EVERY trade so order is known and a second regime accrues._\n")
    p = os.path.join(OUT, "EXIT_CUT_FORENSIC.md")
    _assert_out(p)
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    raise SystemExit(main())
