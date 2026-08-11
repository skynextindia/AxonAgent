# Chart-Pattern Breakout Entry (pattern_breakout_v1_1R)

Built 2026-08-12. The live engine's primary entry path as of this date
(`entry_source: "breakout"` in `axonai/default_config.py`). The legacy tick-fade
entry remains in the code but is gated off (set `entry_source` to `"fade"` or
`"both"` to re-arm it).

## Why

Two candidate signals were re-tested 2026-08-12 on ~3 weeks of out-of-sample
data (train/test split 2026-07-21):

| Signal | OOS result | Verdict |
|---|---|---|
| Tick-fade / sweeps-lead (live engine's mechanism) | test spread ~0, t~0, cost-negative | **FALSIFIED** (agrees with arxiv 2605.04004) |
| Chart-pattern neckline break, fixed 1R bracket | +2.70p/trade all pairs (t=1.68, n=142); **+6.06p ex-GBP (t=3.10, n=89)**; persists W29-W32; both regimes; robust to +1.5p slippage | **SURVIVED** |

Full record: `reports/shadow_patterns.csv` (227 resolved paper trades),
regenerable via `python -m axonai.scripts.shadow_pattern_logger`.

## The trade

- **Detect**: zigzag-pivot chart patterns (double/triple top-bottom, H&S + inverse,
  triangles, wedges, rectangle) on live M15 bars; fire when the just-closed bar is
  the first close through the neckline. Geometry: `axonai/realtime/chart_patterns.py`
  (shared verbatim with the offline validator — do not modify without re-validating).
- **Enter**: market order on the break-bar close. Detector: `axonai/realtime/pattern_breakout_entry.py`.
- **Bracket**: SL = structural pattern extreme, TP = neckline +/- 1R. Broker-side.
  Sizing = existing 1%-risk engine off the SL distance.
- **Manage**: nothing. No trailing, no engine thesis exits, rides through EOD.
  Only a 15h time-stop (= the sim's 60-bar scratch window).
- **Pairs**: EURUSD, USDJPY, AUDUSD. GBPUSD excluded (-2.94p OOS; its wider real
  cost is not modeled). GBPUSD still runs for data collection, it just cannot trade.
- **Guards that still apply**: news blackout, post-loss cooldown, one position per
  symbol, portfolio caps (concurrent/daily-loss/currency-exposure), circuit breaker.

## Wiring map

- `daemon.py` init: `PatternBreakoutDetector` per symbol.
- `daemon.py _on_candle_close`: M15-close hook -> news guard -> `place_breakout` event.
- `daemon.py` event loop: `place_breakout` branch -> `execute_signal(sl=, tp=)`,
  tracked with `_active_trade_system[ticket] = "pattern_breakout"`.
- `daemon.py _manage_trailing_stops`: skips breakout tickets; runs the time-stop.
- `daemon.py _close_all_positions` (EOD): skips breakout tickets.
- Fade gate: `_on_tick` requires `entry_source in ("fade","both")` to enqueue fades.
- Tests: `tests/test_pattern_breakout.py` (live detector == offline geometry).

## What to watch (first live week)

1. **Fill drift**: `trade_analytics.jsonl` rows with
   `strategy_version=pattern_breakout_v1_1R:*` — compare `entry_fill_price` vs the
   logged neckline. The paper edge dies if average adverse drift exceeds ~1.5p.
2. **Frequency**: shadow rate was ~40-70 breaks/week across 4 pairs (3 tradeable).
   Expect roughly 1-4 signals/day on the 3 pairs combined; sessions vary.
3. **Outcome mix**: validated profile ~51% win / 26% loss / 22% scratch at 1R.
   Early SL clusters beyond that mix = fills or spread deviating from the model.
4. **Nothing else should trade**: any position without the pattern_breakout tag
   means the fade path leaked — investigate immediately.

## Known deviations from the validated sim (accepted, review 2026-08-12)

- **One signal per close, one position per symbol, cooldowns, news guard**: live
  trades a strict subset of the validated sample, so live totals will undershoot
  the +6.06p/trade figure; per-trade expectancy is what must hold.
- **A skipped/rejected signal is consumed** (never re-attempted): a break bar is
  the break bar exactly once. Skips are logged (`BREAKOUT SKIPPED`).
- **Adverse-drift gate** (`pattern_breakout_max_drift_pips`, 1.5): entries whose
  fill would sit further past the neckline than the validated slippage stress
  are skipped rather than taken with a skewed bracket.
- **Synthetic bars** (weekend/dead-feed mock ticks) are excluded from detection
  and can never trigger or shape a trade.
- **Restart safety**: open breakout tickets persist in
  `reports/breakout_positions_{SYMBOL}.json` and are re-adopted with their tag
  and UTC entry time, so trailing/EOD can never seize them.
- **Time-stop counts trading time** (weekend hours excluded), approximating the
  sim's 60-bar window for Friday entries.
- **entry_source="both" caveat**: a breakout close calls the fade engine's
  clear_trade() and can wipe a concurrent fade trade's exit state. Default
  ("breakout") is unaffected. Fix before ever running "both".
