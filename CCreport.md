# AxonAI — Codebase & Branch Analysis Report

> **Generated:** 2026-06-18 · **Scope:** Analysis + targeted fixes. **Update 2026-06-18:** high-severity findings verified; Bugs 1 & 2 fixed in working tree (see §5).
> **Repo:** `D:\AXON.AI\Axonagent` · **Branches analyzed:** `main`, `Zscore`, `deepseek-dev`

---

## 1. Executive Summary

**AxonAI** is a 3-layer, event-driven, multi-agent **forex trading daemon** built on top of a fork of TauricResearch's *TradingAgents* framework (rebranded to `axonai`). It connects directly to **MetaTrader 5 (MT5)**, streams raw ticks, runs zero-token mathematical event detection, and fires a **LangGraph** multi-agent LLM pipeline only on high-priority events. A FastAPI + WebSocket glassmorphism dashboard streams everything live.

| Aspect | Finding |
|---|---|
| **Size** | ~29,000 lines of Python across 160 `.py` files; 1 large HTML dashboard |
| **Maturity** | Real-time engine is substantially built; LLM graph is solid; backtest claims are unverified-grade |
| **LLM provider** | **DeepSeek only** in code (factory hard-rejects others), despite README/CHANGELOG claiming OpenAI/Anthropic/Google/Ollama/MiniMax |
| **Active branch** | `Zscore` is the real frontier (11 commits ahead of `main`) |
| **Top risks** | Undefined-variable bug in live lot sizing; a hard-coded mock event injected on every daemon start; a backtest reporting a 95.5% win rate / 62× profit factor that should be treated with extreme skepticism |
| **Hygiene** | Repo root is cluttered with ~25 one-off `fix_*.py` / `patch_*.py` / `_tmp_*` scripts and stray report files |

---

## 2. Branch Topology

```
main  (59 commits) ── production baseline
  │
  ├─ deepseek-dev  (+3)  ── adds economic-calendar protection (CalendarGuard)
  │
  └─ Zscore  (+11, contains all of deepseek-dev + 8 more)  ── the frontier
```

- `Zscore` is **0 behind / 11 ahead** of `main`; `deepseek-dev` is **0 behind / 3 ahead**.
- `Zscore` already includes deepseek-dev's two calendar commits (`e9b1628`, `64bdc05`), so **`Zscore` is effectively a superset of both other branches.**

| Branch | Commits ahead | Theme | Net change vs main |
|---|---|---|---|
| `main` | — | Stable baseline real-time system | — |
| `deepseek-dev` | 3 | Economic-calendar event protection | +1,105 / −38 (6 files) |
| `Zscore` | 11 | Z-score peak detection, decision-intelligence engine, backtester, velocity exits | +3,267 / −352 (25 files) |

**Recommendation:** treat `Zscore` as the de-facto development head. `deepseek-dev` is a strict subset and could be merged or retired; `main` is well behind.

---

## 3. Architecture Overview

```
┌─ LAYER 3 — LLM GRAPH (fires only on HIGH+ events, after cooldown) ─────────┐
│  Trader(TUDOR) → [4 Analysts] → Bull/Bear debate → ResearchMgr(MUNGER)     │
│  → [3 Risk debaters] → PortfolioMgr(DRUCKENMILLER) → BUY/SELL/HOLD          │
├─ LAYER 2 — DETECTION ENGINE (pure math, zero tokens) ──────────────────────┤
│  EventDetector (9–10 pattern types) · LiveWorldState (ATR/RSI/regime)      │
│  LiveMarketEvidence (institutional levels, swings, sessions)               │
├─ LAYER 1 — TICK ENGINE (100 ms MT5 poll) ──────────────────────────────────┤
│  TickEngine → CandleBuilder (M1/M5/M15/H1/H4) → callbacks                  │
└────────────────────────────────────────────────────────────────────────────┘
         ↕ WebSocket ↕  FastAPI Dashboard (api_server.py)
```

**Signal flow:** `MT5 Tick → TickEngine → CandleBuilder → EventDetector → queue.Queue → AxonDaemon → GraphExecutor → LangGraph → PM Decision → TradeExecutor → Dashboard`

### Layer 1 — Tick Engine (`realtime/tick_engine.py`)
- Daemon thread polls MT5 every 100 ms; builds M1/M5/M15/H1/H4 OHLCV candles (500-bar deques), 10k-tick rolling buffer.
- Computes order imbalance over 10 s / 60 s / 300 s windows.
- Fires `on_tick_callback` per tick, `on_candle_close_callback` on period boundaries.

### Layer 2 — Detection (`realtime/event_detector.py`, `live_state.py`)
- **EventDetector** emits 9–10 event types: LEVEL_BREACH, STRUCTURE_BREAK, SWEEP_DETECTED, VOLATILITY_SPIKE, CANDLE_PATTERN, REGIME_SHIFT, SESSION_TRANSITION, SPREAD_CHANGE, MOMENTUM_DIVERGENCE, and (Zscore) PEAK_DETECTION.
- **LiveWorldState** (`live_state.py:71+`): ATR-14, EMA, RSI, regime scoring (trending/ranging/breakout/compression/panic), session/DST tracking, and a composite **belief-score gate** (35 % regime + 25 % session + 20 % trend + 20 % spread).
- **LiveMarketEvidence** (`live_state.py:580+`): six institutional level types (PDH/PDL, PWH/PWL, ASH/ASL, ROUND, H4/M15 swings), per-level behavior tracking (attacks/rejections/absorption).

### Layer 3 — Multi-Agent Graph (`graph/setup.py`, `agents/`)
Sequential DAG wired in `setup.py:67-173`:
```
START → Market → Sentiment → News → Fundamentals analysts
      → EvidenceCompressor
      → Bull(BUFFETT) → Bear(SOROS) → ResearchMgr(MUNGER)
      → Trader(TUDOR)
      → [Aggressive ∥ Conservative] → Neutral → PortfolioMgr(DRUCKENMILLER) → END
```
- **Structured outputs** (`agents/utils/structured.py`): 3-tier fallback — native `with_structured_output()` → JSON-schema re-prompt → free-text. Pydantic schemas: `MungerVerdict`, `TudorExecution`, `DruckenmillerDecision` (`agents/schemas.py`).
- **EvidenceCompressor** (`graph/evidence_compressor.py`): pure-Python pre-processor — extracts summary paragraphs, strips data tables, truncates to ~150 words, pulls macro-event keywords, then `RemoveMessage()`s old messages to prevent token bloat. README claims ~80 % token savings (estimate is a rough `words × 1.3` heuristic, so the real figure is likely lower).

### Layer 4 — Execution (`realtime/trade_executor.py`, `risk_guard.py`)
- Signal → `mt5.ORDER_TYPE_BUY/SELL` market order; FOK with IOC retry; GTC lifetime.
- Two executors per symbol (magic `123456` base / `123457` opt) so two systems trade independently.
- ATR-based SL/TP (SL = max(ATR, 8 pips), TP = max(2×ATR, 16 pips)); dynamic lot sizing from equity × 1 % risk, clipped to [0.01, 0.10].
- `RiskGuard` circuit breaker halts on daily drawdown > ~5 %.

### Dashboard (`realtime/api_server.py`)
- FastAPI in a background thread; `/ws` WebSocket broadcasts ticks, regime, levels, candles, events, agent traces, decisions.
- In-memory history cache (last 30 events, last 50 agent traces); recursive numpy→native JSON conversion; throttles regime/account payloads every 5 ticks.
- REST: `GET/POST /config`, `GET /status`, `POST /trigger` (manual event injection).

### Concurrency model
- Main thread = blocking event loop on `queue.Queue(maxsize=100)`.
- TickEngine thread = producer; FastAPI/Uvicorn threads = dashboard; optional WSL→Windows bridge thread.
- **No explicit locks** — relies on the GIL. State snapshots use `copy.deepcopy()` per graph run (expensive, and a theoretical read-tear risk vs. the tick thread).

---

## 4. What Each Branch Adds (`Zscore` highlights)

### `calendar_guard.py` (deepseek-dev + Zscore)
ForexFactory economic-calendar protection. Fetches the live weekly JSON feed, blocks new entries before High/Med/Low-impact events, force-closes open positions ~15 min before, and resumes ~30 min after. Logs outcomes to `reports/calendar_outcomes.jsonl`.

### `decision_intelligence.py` (Zscore only)
A context/execution-decision engine:
- **MarketContextEngine** classifies state into BREAKOUT / EXHAUSTION / REVERSAL / PULLBACK / TREND_CONTINUATION / RANGE_NOISE from H4/H1 alignment + regime + event type.
- **MarketStateMachine** tracks transitions over a 10-bar window with confidence scores.
- **ExecutionDecisionLayer** turns events into trade decisions with full explainability (`why_trade`, `why_not_wait`, supporting/risk factors), S/R proximity, daily-trend alignment, and empirical reversal-structure checks (wick climax, volume stall, V-rebound).

### Z-score peak detector (`peak_detector.py`, Zscore)
- Replaces hard-coded velocity-divergence thresholds with **dynamic Z-score** gating (>2.0 active, >2.5 confirm); log-scaled divergence; tick-volume integration (relative volume can confirm peaks).
- **Fixed `dt = 0.05 s` per tick** decouples math from wall-clock → "broker-portable" and backtest-compatible; loosens thresholds when it detects 60 s-apart (interpolated) ticks.

### Backtester + 1-year XAUUSD report (Zscore)
`backtester.py` drives the real detection components over MT5 M15 bars with synthetic tick interpolation (or fully synthetic Wyckoff-style paths when MT5 is offline).

`reports/1year_bt_XAUUSD_20260614_223601.md` reports:

| Metric | Reported value |
|---|---|
| Total trades | 176 |
| Win rate | **95.5 %** (168 W / 8 L) |
| Net P&L | +12,293 pips |
| Profit factor | **62.5×** |

> ⚠️ **These numbers are not credible as a live-performance estimate.** A 95 %+ win rate and 62× profit factor on a 1-year sample are hallmarks of an **over-fit / look-ahead / synthetic-data artifact**, not edge. The backtester partly generates its own price paths, peak detection runs on interpolated ticks, and there's no evidence of spread/slippage/commission modeling or out-of-sample validation in the report. Treat as a smoke-test of the pipeline, **not** a strategy validation. (README itself still lists "Live Dry-Run Verification" as an open TODO.)

---

## 5. Notable Risks & Findings

> **2026-06-18 update:** Findings 1–3 were verified line-by-line and 1 & 2 were fixed. See status tags below.

### 🔴 High — correctness / safety
1. **Undefined variable in live lot sizing** — `trade_executor.py:137` used `atr_pips` without defining it; the live (non-dry-run, account-present) path raised `NameError` before the order was built, so **every real order crashed**. ✅ **VERIFIED & FIXED** — replaced with `sl_pips = sl_distance / pip`, deriving the stop in pips from the SL distance already computed at L112.
2. **Hard-coded mock event injected on every daemon start** — `daemon.py:543-559` unconditionally enqueued a synthetic `LEVEL_BREACH @ 1.16282 EURUSD` with no guard, firing the graph (and a possible trade) on every startup. ✅ **VERIFIED & FIXED** — now gated behind `realtime_inject_test_event` (default `False`) with a warning log when enabled.
3. **`RiskGuard.is_tripped`** referenced in the executor (`line 43`). ✅ **VERIFIED — NOT A BUG.** The property exists (`risk_guard.py:98-104`) and `self.circuit_breaker = self.risk_guard` (`trade_executor.py:26`), so it resolves correctly. ⚠️ **Reclassified to 🟡:** `is_tripped` returns `False` whenever `current_equity == 0.0`, and `update_equity` only runs when an MT5 terminal is present (`L38-41`) — so in a **dry-run without an MT5 terminal the circuit breaker is silently always-off.**
4. **No retry / fallback on graph failure** — `graph_executor.py:~165` drops the event silently if the LLM times out; no degraded signal.

### 🟠 Medium — robustness
5. **DeepSeek-only reality vs. multi-provider docs** — `llm_clients/factory.py:~35` raises `ValueError` for any provider ≠ `deepseek`. README/CHANGELOG advertise OpenAI/Anthropic/Google/Ollama/MiniMax that aren't implemented as clients. Misleading and a setup trap.
6. **Portfolio Manager & Research Manager bypass the structured-output fallback wrapper** (`portfolio_manager.py:~100`, `research_manager.py:~58`) — they call `structured_llm.invoke()` directly and on failure return a hard-coded error/HOLD dict instead of the JSON-reprompt fallback the Trader uses.
7. **`analyst_concurrency_limit` is dead config** — passed into `GraphSetup` but never used; analysts are wired sequentially. "4 parallel analysts" in the README is aspirational at the graph level.
8. **Trailing stop only moves to breakeven +1 pip** (`daemon.py:~1160`) rather than truly trailing — mislabeled behavior.
9. **Event queue overflow is silent** — `maxsize=100`; bursts drop events with only a warning.
10. **Checkpointer swallows SQLite errors** (`graph/checkpointer.py:~87`) — failed checkpoint clears report success.
11. **DST logic duplicated** across `daemon.py` and `live_state.py` with hard-coded US/EU rules; broker-offset detection defaults to 0 on failure → session gating can be wrong.

### 🟡 Low — hygiene / maintainability
12. **Repo-root clutter** — ~25 throwaway scripts (`fix_js.py`…`fix_js5.py`, `patch_daemon.py`, `restore_daemon.py`, `_tmp_script.js`, `_git_script*.js`, etc.), multiple stray reports (`report.md`, `newreport.md`, `phasereport.md`), and a corrupted-looking tracked file named `yncio, websockets, json`. These should be moved to `scratch/` or deleted and gitignored.
13. **Token-savings claims** rest on a `words × 1.3` approximation — directionally right, numerically soft.
14. **`copy.deepcopy()` of full state per graph run** — fine now, will not scale to multi-pair coexistence (a stated roadmap goal).

---

## 6. Test Coverage Snapshot

`tests/` holds ~28 test modules covering structured agents, model validation/autodetect, capabilities, dataflows config, checkpoint resume, realtime core, peak detector, signal processing, daemon E2E, trade execution, and backtest. `Zscore` adds `test_calendar_guard.py`, `test_structure_alignment.py`, `test_velocity_management.py`. Coverage of the **math/detection** layer is decent; coverage of **live MT5 order routing** is necessarily thin (hardware-dependent). *Note: the report did not execute the suite — it was not run as part of this read-only analysis.*

---

## 7. Recommendations (prioritized)

1. **Fix the `atr_pips` undefined bug and the startup mock-event injection before any live/dry-run trading.** Both directly affect order placement.
2. **Verify `RiskGuard.is_tripped` exists** — a broken circuit breaker is the worst silent failure for a trading bot.
3. **Reconcile docs with reality:** either implement the advertised LLM providers or update README/CHANGELOG to state DeepSeek-only.
4. **Stop trusting the 95.5 %/62× backtest.** Re-run with real (non-synthetic) ticks, spread/slippage/commission, and an out-of-sample split before drawing any conclusion about edge.
5. **Consolidate branches:** merge or retire `deepseek-dev` (subset of `Zscore`); decide whether `Zscore` becomes the new `main`.
6. **Clean the repo root** — move one-off scripts to `scratch/`, delete the corrupted `yncio, websockets, json` file, gitignore temp artifacts.
7. **Add fallback paths** to PM/Research-Manager structured calls and a retry/degraded-signal on graph failure.

---

## Appendix — Key File Map

| Concern | File |
|---|---|
| Daemon / event loop | `axonai/realtime/daemon.py` |
| Tick ingestion & candles | `axonai/realtime/tick_engine.py` |
| Math event detection | `axonai/realtime/event_detector.py` |
| Live state / evidence | `axonai/realtime/live_state.py` |
| Peak detection (Z-score) | `axonai/realtime/peak_detector.py` |
| Decision intelligence (Zscore) | `axonai/realtime/decision_intelligence.py` |
| Calendar guard | `axonai/realtime/calendar_guard.py` |
| Trade execution | `axonai/realtime/trade_executor.py` |
| Risk circuit breaker | `axonai/realtime/risk_guard.py` |
| Graph wiring | `axonai/graph/setup.py` |
| Structured outputs | `axonai/agents/utils/structured.py`, `axonai/agents/schemas.py` |
| Evidence compression | `axonai/graph/evidence_compressor.py` |
| LLM factory / clients | `axonai/llm_clients/factory.py`, `openai_client.py`, `capabilities.py` |
| Config | `axonai/default_config.py` |
| Dashboard | `axonai/realtime/api_server.py`, `cli/static/index.html` |
| Backtester | `axonai/realtime/backtester.py` |
| 1-yr backtest report | `reports/1year_bt_XAUUSD_20260614_223601.md` (Zscore) |
