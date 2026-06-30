# AxonAI Realtime — Current State

_Last updated: 2026-06-28 • Branch: `velocity` • Last commit: `afe62c6`_

## What this is
Pure-math (no LLM) real-time forex velocity/displacement reversal engine in `axonai/realtime/`.
Live entry point is `run.py` at repo root (NOT `python -m axonai` — package has no `__main__.py`).

**Correct launch command:**
```
python run.py --live --paper --symbol EURUSD
```
(symbol is `EURUSD`, flag is `--paper` not `--paper-trade`, `--live` required.)
Requires Exness MT5 terminal open with EURUSD in Market Watch.

## Completed work (this session)
All committed in `afe62c6`:

1. **Bug #1 (exit_engine.py)** — `legacy.evaluate()` called with correct kwargs
   (current_price, health, regime, liquidity, velocity, displacement, phase,
   phase_confidence, mtf, atr; no `trade_state`). 4/4 smoke tests PASS.
2. **Bug #3 (reversal_model.py:100)** — added `self.latest_snapshot = None` at end
   of `__init__` so `hasattr(model,'latest_snapshot')` succeeds before first tick.
   Populated each tick by daemon.py:847; read at daemon.py:1607.
3. **default_config.py** — `paper_trade: True` (line 153);
   `latency_instrumentation_enabled: True` (line 192, already True).
4. **MTF warm-up (daemon.py)** — new `_backfill_history()` feature (3 edits):
   - `__init__`: `self._warming_up = False` flag.
   - `start()`: calls `self._backfill_history()` after "Step 2/4: Live state initialized",
     BEFORE `tick_engine.start()` (so no live ticks/trades fire during replay).
   - new method `_backfill_history()` before `_on_candle_close`: fetches ~15 days D1
     (seeds PDH/PDL), then replays already-fetched `live_evidence._h4_candles` →
     `_h1_candles` → `_m15_candles` through `reversal_model.on_candle_close`
     (warms MTF EMA20/50 biases, H1 ATR, regime). Logs:
     `AxonDaemon: MTF warm-up complete (N bars). h4_bias=... h1_bias=... m15_bias=... pdh=... pdl=...`
   - Purpose: MTF trend filter (entry_state_machine.py:366) was cold ~8 days at startup
     (biases started at 0.0). Now active from first tick.

## Verified
- `daemon.py` syntax OK; imports clean; `_backfill_history` defined + wired.
- Core tests: `tests/test_realtime_core.py tests/test_trade_execution.py` → 15 passed, 1 skipped.
- `_fetch_bars` supports "D1" via `_TF_MAP` in `axonai/dataflows/mt5_data.py`.

## Known issues (PRE-EXISTING, not regressions)
- `tests/test_smart_cooldown.py` — 9 failures, 2 pass. Confirmed pre-existing via
  `git stash` (identical without our changes). Cooldown-timing config assertions
  (e.g. expected ~270s, got 89.99s). Unrelated to bugs #1/#3 or backfill.

## Pending / next steps
1. **STEP 5 live validation (not done)** — run `python run.py --live --paper --symbol EURUSD`
   ≥5 min with Exness MT5 open. Confirm the warm-up log line appears, then answer:
   tick ingestion, LocationEngine, TradeStateEngine, latency logs, dashboard, errors,
   trade firing. `_backfill_history()` needs a real MT5 run to fully validate (needs
   live_evidence deques populated + D1 fetch).
2. Untracked `docs/Master.txt` exists — not committed; decide if it belongs in repo.

## Key file map
- `run.py` — CLI: `--live`, `--paper`, `--symbol`, `--port 8000`. Flags override config
  (paper_trade @ line 230, realtime_dry_run = not --live @ line 228).
- `reversal_model.py` — orchestrator; `on_candle_close()` (warms MTF, never opens trades),
  `on_tick()` (full pipeline).
- `mtf_context.py` — `_calculate_tf_bias` (EMA20 vs EMA50, scale H4=20/H1=15/M15=10 pips);
  alignment = H4×0.5 + H1×0.3 + M15×0.2; pdh/pdl from D1.
- `entry_state_machine.py` — IDLE→ANOMALY→ARMING→TRIGGERED→INVALIDATED; trend filter
  lines 366-379 (hard-block if both H1&H4 oppose; strong-block if either opposes ±0.4);
  0.3-pip trigger (line 354).
- `daemon.py` — `_backfill_history` (new), `_on_candle_close`, snapshot wiring (:847, :1607).
- `dataflows/mt5_data.py` — `_fetch_bars(symbol, tf_key, start, end)`, `_TF_MAP` incl D1.

## Env
Python 3.14.6 (C:\Python314). Working dir D:\AXON.AI\AxonAgent-Agy.



Resume this session with:
claude --resume 0d4e5331-a40c-471f-85e8-20257edf3261
claude --resume 0d4e5331-a40c-471f-85e8-20257edf3261
 Security guide

 ❯ 1. Yes, I trust this folder ✔
Resume this session with:
claude --resume 0d4e5331-a40c-471f-85e8-20257edf3261
PS C:\Users\rohan>  Esc to cancel


