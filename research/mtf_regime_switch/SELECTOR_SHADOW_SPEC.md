# Selector live shadow — status & arming spec (2026-09-13)

## Status: ALREADY BUILT + ENABLED (no new build needed)
The good-spot selector runs as a live read-only shadow via `wide_tp_mtf_shadow`
(`wide_tp_mtf_shadow_enabled: True`, daemon.py ~2419/2465). Per live EURUSD entry signal it:
- arms a virtual wide bracket at `wtms_sl_pips=20 / wtms_tp_pips=100`,
- stamps the `good_spot_decision(fade_dir, mtf_position, htf=2D, skip_up_buy)` verdict (take/skip/flip),
- resolves fade + with-trend legs over `wtms_max_hold_hours=120`, adverse-first,
- logs one row → `reports/wide_tp_mtf_shadow.jsonl` (goodspot verdict, fade_pips, mfe/mae, mtf ctx).
Gated by `entries_enabled` → records on EURUSD (lead), not USDJPY. NEVER trades.

Readers: `wtms_shadow_report.py`; forward-only scorer `realtrade_selector_forward.py`
(cutoff frozen in `reports/selector_forward_cutoff.txt`).

## Why (validated basis)
Forward OOS split 2026-09-12: selector TAKE +483p (24% win » 16.7% break-even), SKIP −578p on
unseen data — the selector GENERALISES. This shadow re-tests that on LIVE forward fills before the
selector is promoted from passenger to driver. (The volatility threshold is NOT trusted — it overfit
OOS; not part of the arming test.)

## One optional addition
Tag each armed setup with realized 1-day vol (mean M15 range/96 bars) so the vol-gate hypothesis can
be forward-checked later. Optional — the vol gate is unproven; the SELECTOR is the thing being validated.

## Arming criteria (decision rule)
Run `realtrade_selector_forward.py` at each checkpoint. Arm ONLY when, on rows AFTER the frozen cutoff:
1. resolved TAKE rows n >= 40, AND
2. TAKE net-positive after 0.7p cost, AND
3. TAKE net/trade > SKIP net/trade (discrimination holds, sign matches OOS: TAKE +, SKIP −).
If met → build the DRIVER (below). If TAKE turns net-negative or stops discriminating → keep shadow / retire.

## What "arming" means (the real live change, later)
On a TAKE verdict, switch that entry's geometry to wide-TP (SL20/TP100, long hold) instead of the
20/20 scalp — or gate entries by verdict. This is a BIG behavioural change: wide-TP is sparse, ~55%
drawdown, long holds. Arm per-pair, reversible flag, small size first, next flat restart. NOT now.

## Timeline (honest)
Sparse: TAKE setups ~2/week, each needs up to 5 days to resolve → ~40 resolved TAKE rows ≈ 4–6 months
of live forward data from the cutoff. Nothing about live P&L changes until armed — the shadow only logs.
