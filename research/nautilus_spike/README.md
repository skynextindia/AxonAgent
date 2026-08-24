# NautilusTrader breakout backtest — research spike (2026-08-12)

Purpose: re-run the OOS-validated chart-pattern breakout (offline result
**+6.06p/trade, n=89, ex-GBP**) through NautilusTrader's deterministic engine
with a **modeled fill** (two-sided book + spread + FillModel slippage + bar
execution), to see whether the edge survives realistic execution before trusting
the live daemon's real-money measurement.

Isolated: separate venv (`.venv_nautilus`, Python 3.12), separate dir. Reads only
historical parquet bars. **Live stack untouched.**

## Run

```bash
py -3.12 -m venv .venv_nautilus
./.venv_nautilus/Scripts/python -m pip install "nautilus_trader==1.231.0"
./.venv_nautilus/Scripts/python research/nautilus_spike/prep_bars.py        # engine_snapshots -> M15 parquet
./.venv_nautilus/Scripts/python research/nautilus_spike/breakout_backtest.py # ENTRY_MODE=market|limit
```

Reuses the live geometry (`axonai/realtime/chart_patterns.py`) loaded by file path
so the axonai package init (MT5/requests) never runs.

## Result (first realistic run)

| | n | win% | exp (pips) |
|---|---|---|---|
| Offline sim (idealized neckline fill, all independent breaks) | 89 | 51% | **+6.06** |
| Nautilus, realistic fill + **one position at a time** (ex-GBP) | **18** | 39% | **−5.4** |
| — market entry | 18 | 38.9% | −5.42 |
| — limit-at-neckline entry | 18 | 38.9% | −5.40 |

## Findings

1. **The one-position cap is the dominant effect, and it flips the sign.** Market
   vs limit entry gave *identical* results, so fill-drift is NOT the killer here.
   The offline +6.06p counted every pattern break as an independent paper trade,
   including overlapping ones. A real account (this backtest AND the live daemon)
   holds one position at a time, so it takes only a biased ~20% subset (18 of ~89),
   and on this data that subset is **negative**. Part of the paper "edge" was an
   artifact of counting trades the account cannot all take.
2. **Caveat — small n.** 18 trades is a warning, not a verdict. M15-close exit
   granularity and a synthetic 0.6p spread are spike-grade approximations. USD P&L
   is miscomputed for JPY (quote-ccy conversion); use the **pips** column only.
3. **Implication for the LIVE system (running now, pid 49732):** expect far fewer
   and worse trades than the +6.06p study implied, because it has the same
   one-position cap. The live real-fill trades are the true test — watch whether
   the position-capped live sample resembles this −5.4p or the +6.06p paper number.

## Next (if pursued)

- Re-measure the offline edge WITH a one-position-at-a-time constraint (apply the
  same cap to `shadow_pattern_logger`), so paper and live are compared on the same
  accounting. This is the cheapest way to confirm finding #1 at larger n.
- Only then decide whether the breakout is worth keeping live, widening to a
  per-pattern position budget, or shelving.
