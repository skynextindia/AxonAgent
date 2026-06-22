# AxonAgent — System Health & Progress

_Last updated: 2026-06-23 — **LLM/agent full purge COMPLETE**. Pure-math system._

## 0. What changed this session (full purge — APPROVED)

The system is now **pure-math only**. All LLM/agent code removed. Trade decisions are
Rule A+B math (`reversal_model` → `entry_state_machine`). No API key, no LLM deps.

**Files edited:**
| File | Change |
|---|---|
| `run.py` | Removed `--enable-llm` flag + `disable_llm` line. **Added rotating file logging** to `~/.axonai/logs/axon.log` (was console-only — no post-mortem trail). |
| `cli/main.py` | 1558 → 124 lines. Removed `analyze` command, LLM imports, display helpers. Kept `live` + `backtest` only. |
| `cli/utils.py` | 379 → 219 lines. Removed `axonai.llm_clients` imports + all model/provider selectors. |
| `axonai/default_config.py` | Removed `disable_llm` key. (LLM config keys like `llm_provider` left as harmless dead defaults — see note.) |
| `axonai/realtime/api_server.py` | Fixed `/api/pause_llm` + `/api/logs/decisions` (no longer imports deleted `axonai.agents.utils.memory`). |
| `pyproject.toml` | Removed langchain/langgraph deps. Description → "Pure-Math Real-Time Trading Framework". |
| `tests/conftest.py`, `tests/test_ticker_symbol_handling.py` | Removed LLM fixtures/imports. |

**Files deleted:** root `main.py`, `cli/stats_handler.py`, `scripts/`, and 11 LLM test files.

## 1. How the system is launched

| Entry point | Status | Notes |
|---|---|---|
| `run.py` (`start_demo.bat` → `python run.py --direct`) | ✅ WORKS | **The real launcher.** Local Windows + native MT5, direct mode. |
| `cli/main.py` (`axonai live` / `axonai backtest`) | ✅ FIXED | Now imports cleanly (LLM `analyze` command removed). |
| root `main.py` | 🗑️ DELETED | Was an LLM backtest demo. |

## 2. Backend pipeline (verified intact)

```
run.py --direct
  → AxonDaemon.start()                  daemon.py
      → MT5 init (direct) → cold-start state → TickEngine thread → _event_loop()
  → _on_tick (every tick)
      → reversal_model.on_tick() → EntryStateMachine.evaluate()
      → if entry_decision.is_valid_entry → queue "entry" event
  → _event_loop consumes "entry"
      → cooldown / paused / priority gates
      → trade_executor.execute_signal() → send_order() → mt5.order_send()
```

## 3. Configuration (runtime defaults)

| Key | Default | Effect |
|---|---|---|
| `realtime_execution_mode` | **never set** → `"direct"` | Local MT5 `order_send`. |
| `realtime_dry_run` | `True` | Fixed 1.00 lot, **still sends real demo orders**. `--live` enables dynamic sizing. |
| `paper_trade` | `False` | Real send unless `--paper`. |
| `realtime_min_signal_quality` | `0.60` | Confluence floor for entries. |
| `realtime_cooldown_seconds` | `300` | 5 min between entries. |
| `realtime_max_spread_frac` | `0.5` | Entry skipped if spread > 50% of stop. |

## 4. Frontend (dashboard)

| Item | Status |
|---|---|
| `axonai/realtime/api_server.py` | ✅ Imports clean |
| `cli/static/index.html` + charts | ✅ Present |
| WebSocket broadcast (daemon → dashboard) | ✅ Wired |
| `/api/logs/decisions` | ✅ Returns empty list (LLM memory removed) |

## 5. Dual-terminal (Exness feed + MetaQuotes execution) — ROOT CAUSE of non-execution

The `MetaTrader5` Python package binds **one terminal per process**. The intended design:
- **daemon process** → Exness MT5 for market data (ticks/candles), via `mt5_initialize`.
- **`windows/execution_bridge.py` process** → MetaQuotes MT5 for orders, over WebSocket :8766.
- Daemon routes orders through `send_execution_command` when `realtime_execution_mode == "bridge"`.

**The bug:** `run.py` parsed `--exec-path` but **never used it**, never set bridge mode, and never
launched the execution bridge. So in default direct mode, `trade_executor.mt5.order_send()` fired
on the *single* process connection — bound to **Exness (feed)**, not MetaQuotes. Orders went to the
wrong terminal.

**Fixed in `run.py`:** `--exec-path` now (1) auto-launches `execution_bridge.py` against the
MetaQuotes terminal (+ health poll), (2) sets `realtime_execution_mode=bridge` → :8766, (3) keeps
the feed on Exness (`--feed-path`), (4) stops the bridge on Ctrl+C.

**Run the dual-terminal setup (one command):**
```
python run.py --feed-path "C:\...\Exness\terminal64.exe" --exec-path "C:\...\MetaQuotes\terminal64.exe" --live
```

## 6. Test status — ALL GREEN

`python -m pytest -q` → **69 passed, 1 skipped** (0 failures; was 12 collection errors before purge).

The 5 previously-failing tests were diagnosed and fixed (both root causes were real, not stale tests):

| Test(s) | Root cause | Fix |
|---|---|---|
| `test_pattern_intensity` ×5 | `_emit` had a hard 07:00–20:00 **UTC wall-clock gate** that suppressed all events off-hours, even in test mode. Time-of-day dependent. | `event_detector.py` — gate now bypassed when `_test_mode` (production unchanged: `test_mode=False`). |
| `test_trade_execution::test_execute_signal_buy` | `realtime_default_lot_size` config was **never used**; live mode always ran a dynamic formula with a pip-value bug (`*0.10` instead of `*10`). | `trade_executor.py` — live default now uses configured lot; dynamic sizing opt-in via `realtime_dynamic_sizing` (pip-value fixed). |

## 7. Open items / next run
- `run.py` now writes `~/.axonai/logs/axon.log`. After the next live run, grep it for
  `EntryStateMachine: Transition`, `Sending order to MetaTrader 5` (bridge), `Order failed`,
  `spread too wide`, `Position already open` to confirm orders now reach MetaQuotes.
- Verify the execution bridge window shows `Connected to execution terminal` + `Execution Account`.
