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

 ❯ 1. Yes, I trust this folder ✔
Resume this session with:
claude --resume 0d4e5331-a40c-471f-85e8-20257edf3261


 Security guide

 ❯ 1. Yes, I trust this folder ✔
Resume this session with:
claude --resume 0d4e5331-a40c-471f-85e8-20257edf3261
PS C:\Users\rohan>  Esc to cancel
PS C:\Users\rohan>


Fixed and verified — compiles clean, 35/35 tests pass, default path still byte-identical.

Session-bucketed velocity baselines — done (working tree, NOT committed)

What it does: velocity z-score/percentile/vol_pips are now computed against the current session's own history (asian/london/newyork/overlap/rollover — reusing your existing DST-aware live_state labels), not one blended 5000-tick buffer. So a London-open surge is judged against London norms, not stale Asian ticks.

Files: velocity_normalizer.py (buckets + persistence + warm-up fallback), reversal_model.py + daemon.py (one optional session param threaded through; symbol injected into config).

Safety properties baked in:
- Behavior-preserving — session=None (all tests / non-daemon) → global path verbatim. Live path uses the bucket only after ≥500 samples; below that, global values. That's why the 35 tests stay green.
- Persisted + warm-started — reports/velocity_baselines_{symbol}.json, saved every 2000 ticks + on shutdown, reloaded on init.
- Bounded — 5 buckets, decay-cap at 20000 to keep adapting across days.

Adversarial-review defects I fixed:
1. [HIGH] Exploding z in dead-quiet sessions — _bucket_z divided by a ~0 std → z blew to ry tick. Now the denominator is floored at max(std, 10%·mean, 1e-6): real spikes stillflag, jitter doesn't. (Left global _z_score untouched to keep behavior-preservation.)
2. [HIGH] Cross-pair contamination — the daemon CLI path never set config["symbol"], so afile. Now the daemon injects symbol centrally + the normalizer falls back to mt5_symbol.
3. [MED] Loader fragility — one corrupt field no longer aborts the whole warm-start (per-bucket try/except).

Accepted (watch on paper, not blocking): the z/percentile switch from global→bucket is a hard flip at 500 samples (small discontinuity), and the autosave does a small synchronous JSON write every 2000 ticks.

Whole session — uncommitted state

Two independent changes now sit in the working tree, both verified, nothing committed (your rule):
- Entry confluence gate (reversal_model.py, entry_state_machine.py) — fade-at-extremes, f/R, no falling knives.
- Session velocity baselines (velocity_normalizer.py, reversal_model.py, daemon.py).

Paper-test plan (both at once)

python run.py --direct --paper --symbol EURUSD, then check:
1. It takes some trades (entry gate not frozen) and refuses open-space counter-trend sell
2. reports/velocity_baselines_EURUSD.json appears and fills across sessions.
3. At London/NY open, velocity no longer reads as absurdly "unusual" once the bucket warm
4. No is_unusual-spamming during a dead-quiet Asian patch.

Want me to save a session checkpoint, or add the vol_pips debug log so you can capture the calibration number in the same paper run?
Resume this session with:
claude --resume 0d4e5331-a40c-471f-85e8-20257edf3261