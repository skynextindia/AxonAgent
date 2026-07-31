# AxonAI

A deterministic, pure-Python MetaTrader 5 (MT5) forex scalping daemon. AxonAI ingests raw ticks, builds in-memory candles and a live market model, detects microstructure exhaustion events with pure math (no LLM), runs surviving events through a multi-stage entry gauntlet, and executes trades on MT5 with ATR-derived stops and equity-proportional sizing. It runs as **two OS processes** — a lead "brain" account and an execution-only "node" account — that communicate self-generated trade decisions over a localhost WebSocket. **Both accounts are currently DEMO.** This document describes the system as it exists at HEAD of the live branch (`8capTPSL2`); it is neither investment advice nor a product pitch, and it states known defects alongside strengths.

> **Provenance note.** AxonAI began as a rebranded fork of the TauricResearch "TradingAgents" LLM multi-agent framework. That entire LLM/LangGraph stack was **deleted** (commits `a8fd229`, `5d633ca`, July 2026). The old `README.md` described that removed stack and is obsolete; this file replaces it. Residual LLM dependencies and dead directories still exist in the tree — see [Repository layout](#repository-layout) and [Known issues](#known-issues--limitations).

---

## Status & Accounts

| | Lead ("brain") | Exec-node (follower) |
|---|---|---|
| Broker (demo) | Eightcap | FundingPips 2-Step Pro |
| Nominal balance | ~$10,000 | $100,000 |
| Symbols | EURUSD.i, USDJPY.i | (routed from lead) |
| Role | Detects events, gates, executes, **and** mirrors decisions | Executes routed decisions only; re-derives its own sizing/SL/TP |
| Dashboard | `127.0.0.1:8000` | `127.0.0.1:8001` |
| Prop-firm guard | off | on (`--prop-firm`) |

- **Both accounts are DEMO.** No live capital is at risk in the current configuration. Every validated edge and every performance figure below was produced on demo fills with effectively zero slippage.
- **Windows-only.** The launch path, terminal pinning, sleep-prevention, and log rotation are all Windows-specific.
- **Single machine.** Both processes, the mirror, and both dashboards run on one Windows 10 Home box. There is no redundancy, watchdog, or cross-host failover.
- **The running code may not be HEAD.** Live processes run whatever was loaded at launch until manually restarted. The production branch `8capTPSL2` carries local, unpushed commits and uncommitted working-tree edits; the repository at HEAD is not necessarily what is trading.

---

## Architecture

The MetaTrader5 Python binding is a **per-process singleton** — one MT5 connection per OS process — so a second broker account requires a second process. AxonAI runs a **lead** process and an **exec-node** process that talk over `ws://127.0.0.1:8770`.

This is **order routing, not copy trading**: only a self-generated *decision* crosses the wire. No price, SL, TP, lot, or account number from the lead's terminal is ever sent. The node re-derives broker ticker, digits, pip size, ATR-based SL/TP, and lot size from **its own** terminal and equity, and manages its own positions with the same native trailing/EOD/news/prop-guard machinery.

```
        ┌─────────────────────────── LEAD PROCESS (Eightcap ~$10k) ───────────────────────────┐
        │                                                                                       │
  MT5 ──┤ TickEngine ─► LiveWorldState / LiveMarketEvidence ─► EventDetector ─► [event queue]   │
 (feed) │   (100ms poll)      (ATR/EMA/RSI, S/R levels, regime)     PeakDetector                 │
        │                                                                │                       │
        │                                                    AxonDaemon._event_loop              │
        │                                                    (entry gauntlet, below)             │
        │                                                                │                       │
        │                                          MT5TradeExecutor ─► mt5.order_send  (LIVE)     │
        │                                                                │                       │
        │                                    RiskGuard / CorrelationEngine / NewsGuard            │
        │                                                                │                       │
        │        DashboardServer :8000        _mirror_send (decision only)│                      │
        └────────────────────────────────────────────────────────────────┼──────────────────────┘
                                                                          │
                                          ws://127.0.0.1:8770  {cmd, symbol, signal,
                                          MirrorClient ──────►  size_scale, lead_lot, ticket}
                                          (queue max 200, entry TTL 45s,      │
                                           closes never expire, reconcile     ▼
                                           on every (re)connect)     ExecNodeServer
        ┌───────────────────────── EXEC-NODE PROCESS (FundingPips $100k) ─────┼──────────────────┐
        │                                                                     │                  │
  MT5 ──┤ TickEngine ─► LiveWorldState (ATR only; detection OFF)       inject_signal / inject_close│
 (own   │                                                                     │                  │
 feed)  │                                       MT5TradeExecutor ─► mt5.order_send (own sizing)   │
        │                                       (magic +500000, lot = lead_lot × 10, risk-capped) │
        │        DashboardServer :8001          RiskGuard (--prop-firm) / native trailing / EOD   │
        └───────────────────────────────────────────────────────────────────────────────────────┘
```

**Wire protocol** (`ws://127.0.0.1:8770`, localhost only, **no authentication**):

| Verb | Payload |
|---|---|
| `enter` | `{cmd, symbol, signal, size_scale, lead_lot}` |
| `close` | `{cmd, symbol, reason, ticket}` |
| `sync` | `{cmd, open:{SYM:{signal}}, unknown:[SYM]}` |
| `ping` → `pong` | — |

`lead_lot` and `ticket` **do** cross the wire (the wire-contract docstrings in `mirror_client.py`/`exec_node.py` are stale and omit them). `lead_lot` is load-bearing for `--node-lot-multiple` sizing. No prices, SL/TP, or account numbers ever cross.

**Durability.** The lead's `MirrorClient` is fail-open — `send()` never raises or blocks the trading path. Offline decisions queue (max 200); entries expire after 45s, closes never expire; an offline enter+close on the same symbol cancel out. On every (re)connect the client replays the queue, then triggers a **reconcile** against an authoritative snapshot. Reconcile is deliberately asymmetric and conservative:

- node-only position → **always closed** as an unmanaged orphan;
- lead-only position → **alert only** (a late fill buys a move that already happened) unless `mirror_reconcile_enter=True` (default off);
- opposing directions → **flatten, never flip**;
- any pair either side cannot verify → placed in `unknown` and **skipped** (absence from `open` is what authorizes an orphan close, so "unreadable" must never be read as "flat").

Position snapshots are tri-state: `{ok:False}` unverified vs `{ok:True, signal:None}` verified-flat vs `{ok:True, signal, count}` open — because MT5 `positions_get()` returns `()` when flat but `None` on error.

**Limitations of the mirror** (see [Known issues](#known-issues--limitations)): no auth on the socket; reconcile compares direction only (not count/volume); reconcile runs only on (re)connect (no periodic sync over a healthy connection); a routed entry that fails on the node surfaces on the lead only as an INFO log line, not an alert.

---

## Entry pipeline

The lead's `_event_loop` (`daemon.py:829-1195`) runs each event through this gauntlet **in order**. The exec-node short-circuits the entire gauntlet — it discards all self-detected events and enters only via `inject_signal` from the lead.

1. **Event-type filter.** Only `PEAK_DETECTION` events with `peak_type` in `velocity_exhaustion` or `microstructure_exhaustion` pass. This exhaustion-fade signal is the **primary trade trigger**.
2. **S/R proximity.** The event price must be within **5.0 pips** of an active S/R level. *Hardcoded* (`daemon.py:872-893`) — not config-tunable, applied identically to EURUSD and USDJPY despite different pip scales. The only level filter is `is_active`; every tracked level already carries a strength tier of at least 0.2 by construction (`0.2 / 0.4 / 0.7 / 1.0`), so strength is **not** an additional gate.
3. **H4 trend alignment.** Nominally: entry must align with H4 direction, with "sideways" passing both ways. **In practice this gate is inert:** the H4 trend is written to the *inner* evidence object (`live_state.py:1392`, `self._evidence.trend_direction_h4`) but read off the *outer* `LiveMarketEvidence` (`daemon.py:898`, `getattr(self.live_evidence, "trend_direction_h4", "sideways")`), which has no such attribute, property, or `__getattr__` proxy — so it always resolves to the `"sideways"` default and never rejects. See [Known issues](#known-issues--limitations).
4. **Range-extreme gate.** Rejects wrong-end entries: SELL in the bottom 25% or BUY in the top 25% of the prior 20 **closed** M15 candles (~5h). Fails **safe** — refuses the entry when fewer than 10 closed candles exist.
5. **News guard.** Blocks entries within ±15 min of a High-impact event for either pair currency (see [Risk management](#risk-management)).
6. **Session gate.** Session-quality/asian-suppression gating.
7. **SL lockout.** If the pair took a losing stop-out earlier in the trading day, no new entries until the 06:00 IST day roll.
8. **EOD entry cutoff.** No new entries after 17:30 UTC (23:00 IST).
9. **Falling-knife BUY skip** *(config-gated, ON in production)* — vetoes a BUY whose M15 trigger candle closed below its open.
10. **BUY skips** *(config-gated, ON in production)* — panic-regime BUY skip and 08–16 UTC session BUY skip.
11. **Correlation gate.** Cross-pair dollar-direction lock and (followers only) net-USD exposure cap and size scaling.
12. **Execute** → `trade_executor.execute_signal` (which itself checks `RiskGuard.is_tripped`) → mirror `{cmd:enter, ...}` → log to `signals.jsonl` → set cooldown.

**Signal detection caveat.** The exhaustion signal was itself whipsaw-tuned against live behavior in June 2026 (three same-day threshold lowerings; several protective gates temporarily disabled because they "were blocking all intraday entries"). `PeakDetector` also clamps inter-tick `dt ≥ 5s` to `0.05s` (~100× velocity inflation for slow ticks), so quiet-session "exhaustion" events are a mechanically different population from active-session ones.

---

## Risk management

### Position sizing (`trade_executor.py`)

```
SL/TP distance = max(sl_atr_mult × ATR_H1, min_stop_pips)     # sl_atr_mult = 2.0, min_stop = 16 pips
lot            = equity × risk_pct / (SL_pips × $per_pip_per_lot)
lot            = round(lot, 2), then clamped to [realtime_min_lot 1.0, per-pair max_lot 2.0]
```

- **Fixed 1:1 R:R.** SL and TP use the same distance formula.
- `enforce_max_stop_pips` (**ON in production**, `--enforce-max-stop`) caps *both* SL and TP at per-pair `max_stop_pips`: EURUSD 16, **USDJPY 10**. Because USDJPY's 16-pip floor exceeds its 10-pip cap, enforcement yields a **constant 10-pip SL/TP** for USDJPY.
- `$per_pip_per_lot`: EURUSD pinned at $10; USDJPY derived from live price (~$6.5–7).
- **The 1.0-lot floor overrides the risk-% target on the small account.** A 1.0-lot EURUSD trade with a 16-pip stop risks ~$160 — ~1.6% of a $10k account regardless of `risk_pct`, rising toward ~3% as equity draws down. `risk_pct` is not a hard ceiling on the lead.

### Node lot mirroring

With `--node-lot-multiple 10`, node entries size to `lead_lot × 10`, rounded to 0.01, clamped to `[min_lot, exec_node_max_lot=12.0]`, then trimmed by the risk caps. This **overrides** risk-based sizing and correlation scaling on the node.

### Node risk caps (`--risk-cap-per-trade` / `--risk-cap-combined`, default off)

Stop-risk ceilings in dollars: per-trade ≤ `X% × equity`, combined open stop-risk (all magics) ≤ `Y% × equity − open_risk`. Entries shrink-to-fit; blocked only if even `min_lot` exceeds the budget. Positions trailed to breakeven contribute 0 open risk. **These are stop-loss-dollar limits, not notional/margin limits** — on a 10-pip USDJPY stop they do not bind at 12 lots, so the 12-lot clamp is the only effective limiter (and it has produced broker-rejected orders — see below).

### RiskGuard — daily breaker + prop-firm layer (`risk_guard.py`)

- **Non-prop:** trips at 5% daily drawdown or $500 daily loss.
- **Prop layer** (`--prop-firm`): overall drawdown floor and daily-loss limit measured on the **broker server day**, with a 20% safety buffer so tripwires sit *below* the firm's real lines. Production (FundingPips 2-Step Pro) config: `--prop-max-drawdown-pct 6.0 --prop-daily-loss-pct 3.0` → **effective trips at 4.8% overall / 2.4% daily**.
- **Fails closed.** Without `--prop-initial-balance` (or valid persisted state) the guard **halts all trading** rather than inferring the drawdown floor from a possibly drawn-down live balance. Corrupt state never re-baselines. A foreign/implausible daily baseline (outside 0.5×–2× live equity) is reseeded — a hardening from a real 2026-07-30 incident where a 100k baseline made a 10k account reject every order for a day.
- **Breach flatten latches only after positions are verified gone** (`open_count == 0`), retrying every ~2s otherwise — a close that may have been requoted mid-move is never assumed successful.

### CorrelationEngine (`correlation_engine.py`)

Dollar-direction lock: while any position is open, a new entry (lead included) must agree on signed USD direction with every open position. Follower-only extras: lead-bias veto, $200k net-USD exposure cap with proportional shrink, size scale `= 1 − 0.5|corr|` floored at 0.25. Pearson over 100 H1 bars, refreshed every 300s.

### NewsGuard (`news_guard.py`)

Single free ForexFactory JSON feed, 6h refresh / 20-min failure backoff. Blocks entries ±15 min of **High-impact** events for either pair currency; a separate path force-flattens **all** positions 0–5 min before an event. **Fails open** — with no calendar loaded, `should_block_entry` returns False and trading proceeds unprotected (logged, not alerted). Medium-impact events and central-bank speakers are not gated.

### EOD schedule (all UTC)

| Point | Time | Action |
|---|---|---|
| Entry cutoff | 17:30 (23:00 IST) | Hold existing, no new entries |
| Force-flatten | ~20:55 (DST-aware, 5 min before NY rollover) | Flatten all, once per trading day |
| Resume / day roll | 00:30 (06:00 IST) | Clears SL lockout, rolls trading day |

Pre-news and EOD flattens fire **market close-all orders in the two widest-spread windows of the day** — never observed under the zero-slippage demo validation.

---

## Validated & rejected experiments

All figures are **counterfactual replays** over ~201 real trades (June 2026 n=57, July 2026 n=144), across two symbols, using M1 broker deal history (`analyze_trades.py`). **There is no significance testing, no per-cell sample sizes, and no dispersion reported.** June is *not* a strict holdout — it participated in the filter-acceptance rule (accept if negative in **both** months). **August live is the first genuine out-of-sample test** of essentially the entire shipped tuning stack.

### Shipped (config-gated, ON via `run_system.bat` option 6)

| Change | Config gate | Reported effect | Caveats |
|---|---|---|---|
| Falling-knife BUY skip | `--skip-falling-knife` | −2.0 pips/trade avoided across a 197-trade in-sample replay subset; ~+37% net | Small per-trade effect vs large dispersion; sign could flip on a handful of trades. Partly a directional bet — both validation months down-trended |
| Wider trail 0.35→1.0×ATR | `--trail-dist 1.0` → `trail_dist_atr_mult_override` | ~+47% net at same win rate over the 201-trade M1 replay | Measured under **uncapped** exits; largely cannot arm under the shipped 10-pip USDJPY cap |
| Session BUY skip (08–16 UTC) | `--skip-session-buy` | +77 pips (June) / +66 (July) vs +38/+1 for the old 07–12 window | Old window was validated on a **+3h-wrong clock**; re-derived after the fix (see History). Partly a directional bet — both validation months down-trended |
| Panic BUY skip | `--skip-panic-buy` | −58 / −30 pips avoided (June/July) | Partly a directional bet — both validation months down-trended |
| 16/10-pip SL/TP caps | `--enforce-max-stop` | **no replay numbers of its own** | Ships enabled; neutralizes the validated 1.0×ATR trail on USDJPY |
| 1.9% per-trade risk | `--risk-pct 1.9` | not replay-validated | Sample was generated at 1.0% / 1.0-lot scale; new leverage regime |
| Node 10× lead-lot mirror | `--node-lot-multiple 10` | not replay-validated | Produced a broker-rejected 12-lot order |
| Node stop-risk caps 1.9%/3.8% | `--risk-cap-per-trade` / `--risk-cap-combined` | — | Stop-risk dollars, not notional |

**The deployed configuration was never validated as a package.** Caps + 1.0×ATR trail + 1.9% risk + 10× node mirror were switched on together; no experiment tested that joint regime.

### Tested and REJECTED (failed out-of-sample — do not re-try)

| Experiment | Why it failed | Implication |
|---|---|---|
| Confirmation entry (require +2 pips before entering) | Destroyed the entry edge | Early trade excursion does not separate winners from losers |
| No-progress abort (cut if not +2p in N minutes) | A dead loser and a slow winner are indistinguishable early | Trades need adverse-excursion room — in tension with the shipped 10-pip USDJPY stop |

The no-progress abort remains in code but is **double-disabled** (`entry_noprogress_abort=False` **and** `noprogress_abort_notice_only=True`).

### Offline BacktestEngine — do NOT cite as live performance

`BacktestEngine` replays historical M15 through the real detection stack, but it has **drifted** from the live path: it hardcodes SL `= max(1.0×ATR, 8 pips)` / TP `= max(2.0×ATR, 16 pips)` (live is `2.0×ATR`/16-pip, **1:1** R:R), it lacks every live gate added since July (news, correlation, range-extreme, falling-knife, BUY skips, SL lockout, EOD cutoff), and it cannot express the 1.0×ATR trail without source edits. Its headline figures (e.g. AGENTS.md's 77.4% WR / PF 2.27 / +86.5 pips) describe **that simulator**, not the deployed strategy.

---

## Operations / runbook

**Launch is Windows-only, `.venv`-pinned, and manual.** Never launch with bare `python` — always the `.venv` interpreter via `run_system.bat`.

`run_system.bat` menu:

| Option | Action |
|---|---|
| 1 | Single-pair lead — **EURUSD only** (`--direct`, validated flags) |
| 2 | Multi-pair lead — **EURUSD + USDJPY**, one daemon thread per pair over a shared MT5 connection (correlation engine + per-pair calibration active) |
| 3 | Analyze trade history (MAE/MFE/drawdown; read-only) |
| 4 | Run the offline intraday backtester |
| 5 | Legacy WSL bridge mode (semi-legacy) |
| **6** | **Dual launch**: opens dashboards 8000+8001, starts Eightcap lead, waits 8s, starts FundingPips exec-node |
| 7 | **Kill-all** (requires typing `YES`): kills every `python.exe` whose command line matches `run.py`, verifies ports 8000/8001 free; never touches MT5 terminals |

**Option 6 launch parameters** (verbatim from `run_system.bat`, terminal paths abbreviated):

- Lead: `--direct --symbols "EURUSD,USDJPY" --mt5-path <Eightcap terminal> --mirror-url ws://127.0.0.1:8770 --skip-falling-knife --trail-dist 1.0 --enforce-max-stop --risk-pct 1.9 --skip-panic-buy --skip-session-buy`
- Node: `--direct --symbols "EURUSD,USDJPY" --mt5-path <MetaTrader 5 terminal> --port 8001 --exec-node --prop-firm --prop-initial-balance 100000 --prop-max-drawdown-pct 6.0 --prop-daily-loss-pct 3.0 --trail-dist 1.0 --risk-cap-per-trade 1.9 --risk-cap-combined 3.8 --enforce-max-stop --node-lot-multiple 10 --risk-pct 1.9`

**Operator obligations:**

- **Monitor manually.** The only observability surface is the human dashboard (`:8000` lead, `:8001` node). There is **no** `/healthz`, no metrics endpoint, no external liveness probe.
- **Alerting is currently non-functional.** `send_alert` pushes to Telegram/webhook only if `alert_telegram_token` / `alert_telegram_chat_id` / `alert_webhook_url` are set — and these keys are defined in **no** config file or CLI flag. All alerts (dead pair thread, rejected SL modify, reconcile divergence) degrade to a local log line no one is watching.
- **No auto-restart, no crash/reboot recovery.** `run.py` has no self-spawn; the supervisor detects a dead pair thread but does **not** restart it; `AxonDaemon.start()` returns silently on MT5-init failure (a pair is disabled while the process still looks alive). A Windows Update reboot or power event stops both accounts. Recovery is entirely manual. Open positions keep their broker-side entry SL/TP but stop being trailed/flattened.
- **Restart after every fix.** Live processes run the code loaded at launch. There is no hot-reload and no version indicator; after any change, kill (option 7) and relaunch, then verify deliberately.
- **The singleton port guard** refuses duplicate launches on the dashboard port (and, for the node, port 8770), exiting code 2. Option 7's verify step checks ports 8000/8001 but **not** the mirror port 8770.
- **Weekend / stale-feed prices are synthetic.** On weekends or any feed staleness > 10s, the tick engine fabricates random-walk ticks that flow through the **live** pipeline — dashboards show moving prices and trailing-stop management runs on fabricated ticks. This is by design; it is not broker data.

---

## Config reference

Config is a single flat dict (`axonai/default_config.py`) with a per-pair overlay (`SYMBOL_CALIBRATION` → `resolve_symbol_config`) and CLI overrides (`run.py`). Gates that matter:

| Flag → config key | Default | Production | Meaning |
|---|---|---|---|
| `--skip-falling-knife` | OFF | ON (lead) | Veto BUY into a red M15 trigger candle |
| `--skip-panic-buy` / `--skip-session-buy` | OFF | ON (lead) | Panic-regime / 08–16 UTC BUY skips |
| `--trail-dist` → `trail_dist_atr_mult_override` | 0.35 | 1.0 | Trailing distance in ATR multiples |
| `--enforce-max-stop` → `enforce_max_stop_pips` | False | True | Cap SL **and** TP at per-pair `max_stop_pips` (EURUSD 16 / USDJPY 10) |
| `--risk-pct N` → `realtime_risk_pct_override` | 0.01 (1%) | 1.9% | Account-wide risk fraction (stored as N/100) |
| `--node-lot-multiple` → `exec_node_lot_multiple` | None | 10 (node) | Node lot = `lead_lot × N`, clamped to 12.0 |
| `--risk-cap-per-trade` / `--risk-cap-combined` | None | 1.9 / 3.8 (node) | Node stop-risk ceilings (% equity) |
| `--prop-firm` / `--prop-initial-balance` | off / None | on / 100000 (node) | Enable prop guard; **required** or node halts |
| `--prop-max-drawdown-pct` / `--prop-daily-loss-pct` | 10 / 5 | 6 / 3 (node) | Firm lines (buffered −20% in effect) |

**Magic numbers:** base 123456; EURUSD 123457, USDJPY 123458; exec-node adds +500000 (→ 623457 / 623458), keeping each process's position filters disjoint.
**Ports:** 8000 lead dashboard, 8001 node dashboard, 8770 mirror WS, 8765 legacy WSL bridge.

### Config hazards (read before trusting the file)

- **`realtime_dry_run` is a dead, misleading flag.** `run.py:292` hardcodes it `True` on every launch, yet the executor sends real orders regardless (`daemon.py:982` computes `is_dry_run` and never uses it; the flag only gates JSONL logging). **There is no paper-trading mode. Every launch places real orders.**
- **Defaults contradict code fallbacks:** `realtime_cooldown_seconds` 10 (config) vs 300 (daemon fallback); `realtime_suppress_asian` False (config) vs True (code). The EMA trend calc actually uses the config 12/26; `live_state`'s `.get()` fallback literals (20/50) are dead because those config keys exist, while its EMA *seeding* hardcodes `span=20` — a seed-vs-live inconsistency, not a 12/26-vs-20/50 divergence in the trend calc.
- **Some guard families have no config entry at all** (`news_guard_*`, `risk_max_daily_*`, `level_tracker_*`) — their defaults live in class constructors.
- **A stale config comment** still says `prop_initial_balance` seeds from the account's first balance; the guard was rewritten to **refuse** exactly that. A reader trusting the comment would omit `--prop-initial-balance` and the node would halt.

---

## Repository layout

### Live path

```
run.py                          CLI launcher, port guard, log setup (NO auto-restart)
run_system.bat                  Operator entry point (options 1/2/5/6/7)
axonai/default_config.py        Flat config + SYMBOL_CALIBRATION (the config the running code actually reads — defaults live here, not in the docs)
axonai/realtime/
  daemon.py                     AxonDaemon (2,383-line core: gauntlet, trailing, EOD, mirror, telemetry)
  supervisor.py                 DaemonSupervisor: N pair-threads, one MT5 conn, one RiskGuard
  tick_engine.py                TickEngine + CandleBuilder (100ms poll, synthetic-tick fallback)
  live_state.py                 LiveWorldState + LiveMarketEvidence (indicators, S/R taxonomy)
  event_detector.py             EventDetector (pure-math event rules)
  peak_detector.py              PeakDetector (exhaustion signal — primary trigger)
  level_tracker.py              LevelBehaviorTracker (tick microstructure at levels)
  trade_executor.py             MT5TradeExecutor (sizing, SL/TP, order_send)
  risk_guard.py                 RiskGuard (daily breaker + prop-firm layer)
  correlation_engine.py         Cross-pair USD lock / exposure / scaling
  news_guard.py                 ForexFactory calendar blackout
  session_tuner.py              Self-learning session selector (default off)
  mirror_client.py              Lead-side decision forwarder (fail-open, replay queue)
  exec_node.py                  Follower-side WS server + reconcile
  alerts.py                     Telegram/webhook dispatch (currently unconfigured — inert)
  api_server.py                 FastAPI dashboard + WebSocket
axonai/dataflows/               mt5_data.py, evidence_extractor.py, config.py (LIVE); 13 others DEAD
axonai/world_state.py           Pure-math regime/belief scorer
cli/static/index.html           The dashboard HUD (single ~133 KB file)
cli/stats_handler.py            Imported but NEVER instantiated (drags in langchain_core)
```

### Legacy / dead (present in tree, not on the live path)

- `README.md` (old), `CHANGELOG.md`, `details.md`, `AGENTS.md`, `report.md`, `newreport.md`, `phasereport.md`, `brainstorm/BS.txt` — **all describe the deleted LLM stack or are frozen pre-pivot artifacts.** AGENTS.md's backtest parameter table is still accurate *for the backtester* but not for live.
- 13 of 17 `axonai/dataflows/` modules (alpha_vantage×6, `y_finance.py` + `yfinance_news.py`, reddit, forex_social, interface, stockstats_utils, utils) — orphaned LLM-stack tools.
- Root `tick_engine.py` (name-shadows the live module), `mt5_receiver.py`, `windows/mt5_bridge.py`, `_start_dash.py`, `start_bridge.bat`, `start_demo.bat` — WSL/bridge-era or divergent launchers.
- `axonai.egg-info/` (stale; lists 40+ deleted files, exposes a `axonai = cli.main:app` console script whose target is gone), `pyproject.toml` (still declares ~13 dead heavyweight deps: langgraph, langchain-*, backtrader, redis, typer…), `requirements.txt` (literally `.`).
- Two junk files at repo root from botched shell redirects (`yncio...`), ~114 KB each.
- `scratch/*.py` — frozen June-2026 one-off analyzers with hardcoded `d:\work\AxonAI\...` paths and missing imports; not runnable.

---

## Testing

- **23 test modules / 264 test functions / ~4,212 lines.** Commit `70e3f43` reports **233 tests run** with **8 pre-existing errors** — this is the state at HEAD, not a transient failure.
- **No CI, no installed test runner.** Documented run mode: `.venv\Scripts\python.exe -m unittest tests.<module>`. The 8 errors break down as **7 pytest-import failures + 1 stale `GraphExecutor`-patch E2E** (per commit `70e3f43`; `ee2eb5d` calls them "the 8 import errors"). Seven test modules (`test_backtest`, `test_dataflows_config`, `test_env_overrides`, `test_pattern_intensity`, `test_peak_detector`, `test_realtime_core`, `test_safe_ticker_component`) — plus `conftest.py` — `import pytest`, which is installed in no Python on this machine; the 8th, `test_daemon_e2e.py`, does not import pytest but fails on a removed `GraphExecutor` patch target. `conftest.py` (pytest fixtures/markers) never loads under `unittest`.
- **Strengths — incident-driven regression discipline.** Well-locked: prop guard (drawdown/daily/consistency), mirror replay/reconcile (33 tests), range-extreme gate, falling-knife/BUY skips, SL lockout, EOD machine, risk caps, sizing math, the `size_scale=0` inversion guard. Tests document the exact live failure they pin, with dates. The suite catches interface drift (a `lead_lot` signature break broke 5 tests this week).
- **Gaps — the MT5-touching halves are stubbed or unexercised.** `daemon._event_loop` (tick→event→order) and `_manage_trailing_stops` (moves stops on the funded account every tick) have **no working coverage**. `mirror_position_state`, `NewsGuard.should_block_entry`, `CorrelationEngine`'s MT5 refresh, and `alerts.send_alert` are always mocked. Some tests are tautological (3 of 6 peak-detector tests and 2 realtime-core suites assert hand-built or re-implemented values). The dead prop-guard paths (`consistency`, `entry_allowed`, profit-target) are tested but have **no production caller** — coverage there is false assurance.
- **Do not run `test_daemon_e2e.py` or `test_backtest.py`.** Both would attach to a live MT5 terminal if runnable (unpatched `mt5.initialize` / `BacktestEngine.run`). `test_daemon_e2e.py` currently fails on a removed `GraphExecutor` patch target *before* it reaches `mt5.initialize` — accidental protection. Do **not** "fix" it by only repairing the patch: `MetaTrader5.initialize` is unpatched, and two live daemons run on this machine.

---

## Development history

~208 commits, 2026-05-21 → 2026-08-01 (~10 weeks). **~37% of commit subjects (76/208) contain "fix"** (~23%, 6/26, on the live `8capTPSL2` branch alone); a large share of the major features — prop guard, foreign-baseline reseed, the `size_scale=0` guard, the close-fill fallback — were added in direct response to named live incidents. The live line is branch `8capTPSL2` (26 commits, rooted at an orphan squash on 2026-07-05, so `git blame` on most of the live tree stops there).

1. **LLM multi-agent origin** (05-21…05-28): rebranded fork of TauricResearch/TradingAgents — LangGraph analyst/researcher/risk/trader agents.
2. **Realtime microstructure** (05-31…06-14): MT5 Windows bridge, Lightweight-Charts dashboard, Rule A/B dry-run, Z-score peak detection, calendar guard.
3. **Pure-math migration #1** (06-19…06-23): first LLM removal (−17,048 lines), feed/exec terminal decoupling, a squashed baseline re-root.
4. **Velocity intelligence** (06-24…07-04): velocity-physics entry/exit, heavy MT5-binding/deal-sync/sizing bug-fixing. **This era's entry logic was whipsaw-tuned** (three same-day threshold lowerings; protective gates disabled to unblock entries).
5. **Multi-currency "clean" line** (07-05…07-14): 4-FX + Gold stability work — later abandoned for the fresh `8capTPSL2` line.
6. **Live-line reboot + news gating** (07-05…07-16): current lineage begins as an orphan squash; ATR trailing/breakeven core, ±15-min news gating, pre-event flatten.
7. **LLM removal #2 + multi-pair** (07-21…07-25): the LLM stack (which rode in via the squash) is deleted a second time (−5,884 then −4,769 lines); per-pair calibration, single-process multi-pair supervisor, correlation engine, EOD hard-flat, SL lockout. Fix: SL lockout wrongly firing on **profitable** trailed stops the broker labels `sl`.
8. **Prop guard + mirror/exec-node** (07-29…07-30): dual-account architecture, range-extreme gate, A→B mirror + reconcile (TTL 45s), prop guard (fails closed), USD-direction correlation lock, thin exec-node, per-process file isolation, singleton port guard, terminal-path pinning + attach verification, `.venv`-only launch.
9. **Validated tuning + caps** (07-31…08-01, unpushed): falling-knife skip, 1.0×ATR trail, node risk caps, FOK→IOC close fallback; timestamp-integrity fixes.

**Two timestamp bugs that mattered:**

- **Broker-server-time skew (+3h).** MT5 stamps deals with broker wall-clock (UTC+2/+3) in a Unix-epoch field; `datetime.fromtimestamp()` double-added the offset, so every `trade_closed` time was over-reported by **exactly +3h**. Fixed in `ee2eb5d` (`_deal_time_local`), verified against 205 orders' deal epochs with zero variance.
- **The re-validated BUY window.** Because of that +3h skew, the "London BUY 07–12 UTC" session skip had been "validated" on a 3h-wrong clock. Corrected to **08–16 UTC** (`entry_skip_session_buy`), which raised the filter's value from +38/+1 to +77/+66 pips. `ee2eb5d` also added a `timestamp_utc` field to both record types.

**History hazards.** Provenance is destroyed by three orphan roots; there is no protected mainline (`origin/main` stale since 2026-06-25); the live branch is 2 commits ahead of `origin` and unpushed while trading. Safety-critical defects repeatedly reached the live system before being caught (foreign-baseline breaker trip, 181 consecutive close rejects, wrong-terminal attach, a `size_scale=0` full-size inversion). 8 pre-existing test errors have been acknowledged in three commit messages and carried forward rather than fixed.

---

## Known issues & limitations

**Statistical / research**
- All edges rest on ~201 trades over two months that **shared a down-trend**; no significance testing exists. The BUY-side skips are **partly a directional bet** — both validation months were down-trends, so SELL entries dominated the retained edge; the BUY-side contribution is unquantified. In an up-trending regime these skips may suppress the winning side. June is not a strict holdout. **August live is the first true OOS test.**
- The deployed config was **never validated as a package**; the 16/10-pip caps ship with no replay numbers and **neutralize** the validated 1.0×ATR trail on USDJPY (breakeven trigger ≈ 49 pips vs a 10-pip TP).

**Demo → live gap**
- Both accounts are DEMO; all figures assume near-zero slippage. Pre-news (±5 min) and EOD (~20:55 UTC) flattens fire market close-all orders at the widest-spread moments of the day, untested against live fills. USDJPY's constant 10-pip stop can sit inside the rollover spread.

**Sizing / risk holes**
- Node lot has **no notional/margin ceiling**; stop-risk caps are toothless on tight stops, so the 12-lot clamp is the only limiter — and a **12-lot node order has been rejected by the broker (ticket 0)**, a silently-missed trade on the prop account.
- The **FundingPips 45% consistency payout rule is dead code** — `record_trade_result` (its only feed) has no production caller, so the gate always evaluates to "allow", despite the launch banner advertising it.
- **`RiskGuard.is_tripped` fails OPEN when equity reads 0.0** (e.g. a transient `account_info()` failure), bypassing every breaker — including the no-baseline halt — on the next order.
- On the $10k lead, the 1.0-lot floor overrides the 1.9% risk target (~1.6% per trade, rising as equity draws down).

**Correctness / structure**
- **No magic filter in position management.** `_manage_trailing_stops` and `_check_for_closed_positions` operate on **all** positions on the symbol. A manual or foreign-EA position gets auto-trailed; its close is mirrored `{cmd:close}` to the node (flattening the node's legitimate mirrored trade), plus applies cooldown and can engage the day-long SL lockout. `_close_all_positions`, inconsistently, *does* filter by magic. **Manual trading of EURUSD/USDJPY on either account while daemons run will be interfered with.**
- **The H4 "trend" gate is effectively inert.** It reads `trend_direction_h4` from the outer `LiveMarketEvidence` (`daemon.py:898`), but the value is only ever written to the inner `MarketEvidence` (`live_state.py:1392`); with no property or `__getattr__` bridging the two objects, the `getattr(..., "sideways")` default is always returned, so the counter-trend rejection never fires.
- **Synthetic random-walk ticks flow into the live position-management path** on weekend or feed staleness > 10s, driving real SL-modify decisions off invented prices; mock timestamps land in the wrong candle buckets.
- **No thread synchronization** in `live_state.py` (1,559 lines, zero locks): the tick thread mutates level dicts/deques while the daemon thread `deepcopy`s snapshots — torn/raising snapshots are possible.
- `daemon.py` is a 2,383-line god object with three divergent hand-rolled DST/session computations; drift there silently shifts the EOD flatten time.

**Operational**
- **Out-of-band alerting is non-functional** (unconfigured keys). **No auto-restart, no health endpoint, no watchdog, no crash/reboot recovery.** A dead pair thread or failed MT5 init silently disables trading while the process looks healthy. A routed trade that fails on the node is not alerted until the next reconnect.
- **Single Windows 10 Home machine** for both accounts, the mirror, and both dashboards — one reboot/update/power event stops everything.
- **The exec-node control WebSocket has no authentication.** Any local process reaching `127.0.0.1:8770` can open full-size positions or flatten the prop account. It must never be bound to a non-loopback interface.
- **The dashboard Journal tab is mis-wired on the node**: it reads the untagged `signals.jsonl`, so the node dashboard (`:8001`) shows the **lead** account's trades, not its own. `POST /trigger` injects a synthetic CRITICAL event with no auth and no dry-run guard — a single curl to `:8000/trigger` can produce a real trade.
- **Stale live code.** Processes run whatever was loaded at launch; the traded revision is unpushed and exists only on this workstation.

**Data / logs**
- `signals.jsonl` has two record types with an asymmetric join key (`trade_closed.ticket == entry.trade_result.order`, and `trade_result` may be null on failed entries). On `trade_closed` rows, `timestamp_utc` is the **detection** instant (log-write time), not the close instant — do not compute hold-time from it; the corrected close time is the `timestamp` field. Pre-`ee2eb5d` close rows carry the +3h skew.
- The pre-isolation untagged `signals.jsonl` mixes both accounts' P&L with **no account field** — do not aggregate over full history without partitioning by era.
- `signals.jsonl` / `signals.log` / `dry_run_session.jsonl` are **not rotated or size-capped** (only `daemon.log` rotates, 10 MB × 10). No retention policy.

---

## Disclaimer

AxonAI is an experimental, single-operator trading daemon. **Both accounts described here are DEMO; no live capital is at risk in the current configuration.** Nothing in this repository or this document is investment or financial advice. All performance figures are counterfactual replays over a small sample (~201 trades, two down-trending months, no significance testing) produced on demo fills with effectively zero slippage; they do not describe live expectancy and must not be read as such. The system places **real orders on every launch** (there is no paper-trading mode), routes trade decisions between two operator-owned accounts, requires an attended operator, and has multiple documented failure modes — including non-functional alerting, no auto-restart, a fail-open risk breaker, and an unauthenticated local control socket. Do not deploy it against a funded or live-capital account without independently addressing the issues in [Known issues & limitations](#known-issues--limitations).
