# Graph Report - D:\AXON.AI\AxonAgent-Agy  (2026-07-06)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1270 nodes · 2709 edges · 68 communities (61 shown, 7 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 276 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `20f6691c`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 65|Community 65]]

## God Nodes (most connected - your core abstractions)
1. `NormalizedVelocity` - 83 edges
2. `DisplacementState` - 74 edges
3. `LiveCandle` - 69 edges
4. `RegimeState` - 53 edges
5. `AxonDaemon` - 52 edges
6. `LiquidityState` - 43 edges
7. `EventDetector` - 41 edges
8. `EntryStateMachine` - 40 edges
9. `MarketContextBuilder` - 40 edges
10. `ReversalModel` - 40 edges

## Surprising Connections (you probably didn't know these)
- `BridgeDataCollector` --uses--> `BacktestEngine`  [INFERRED]
  run_bridge_backtest.py → axonai/realtime/backtester.py
- `TestDaemonE2E` --uses--> `AxonDaemon`  [INFERRED]
  tests/test_daemon_e2e.py → axonai/realtime/daemon.py
- `TestSmartCooldown` --uses--> `AxonDaemon`  [INFERRED]
  tests/test_smart_cooldown.py → axonai/realtime/daemon.py
- `TestEntryStateMachineWithMarketContext` --uses--> `DisplacementState`  [INFERRED]
  tests/test_entry_state_machine_with_market_context.py → axonai/realtime/displacement_engine.py
- `TestMarketContext` --uses--> `DisplacementState`  [INFERRED]
  tests/test_market_context.py → axonai/realtime/displacement_engine.py

## Import Cycles
- None detected.

## Communities (68 total, 7 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.07
Nodes (43): AdaptiveExitManager, ExitDecision, Adaptive Exit Engine.  Replaces fixed ATR TP/SL. Uses the Trade Health Monitor a, Evaluate if we should hold, adjust SL/TP, or force close., Output of the exit evaluation., Manages active trades, adjusting targets or cutting losses dynamically., Register the trade to track., Clear active tracking. (+35 more)

### Community 1 - "Community 1"
Cohesion: 0.07
Nodes (36): EntryDecision, EntryStateMachine, datetime, Entry State Machine.  Replaces the legacy stateless boolean EntryGate. Impleme, Evaluate conditions and transition states., Evaluate entry decision using MarketContext quality scores.          Uses qual, Look for the initial anomaly (Microstructure Peak)., Wait for the anomaly to form a trap or show absorption. (+28 more)

### Community 2 - "Community 2"
Cohesion: 0.08
Nodes (26): MarketContextBuilder, Build MarketContext with quality scores from engine outputs., Determine overall market context verdict.          Matrix:         - confidence, Track how long displacement has been in current classification., Is the reversal opportunity closing/expiring?          Windows close when:, How many ticks until this signal becomes stale/invalid?          Signals expire, When some engines agree, score should reflect the ratio., When no engines agree, score should be low. (+18 more)

### Community 3 - "Community 3"
Cohesion: 0.06
Nodes (35): DisplacementBufferEngine, DynamicDisplacementThresholds, Dynamic Displacement Buffer Engine for Adaptive Entry Thresholds.  Replaces stat, Compute dynamic displacement thresholds from regime.          Args:, Output: dynamic entry thresholds based on market regime., Computes dynamic displacement thresholds from market regime.      Adapts impulse, Args:             config: Optional config dict with:               - impulse_rat, DisplacementEngine (+27 more)

### Community 4 - "Community 4"
Cohesion: 0.07
Nodes (27): EventDetector, datetime, Set pip multiplier based on pair type., Lightweight per-tick checks., Invoke microstructure peak and climax exhaustion detector for both systems., Structural checks on candle close., Detects structural market events from live state.      Detection rules (all pu, Check for breach of active institutional levels on tick.          Uses LevelBe (+19 more)

### Community 5 - "Community 5"
Cohesion: 0.08
Nodes (39): _ensure_symbol_visible(), _fetch_bars(), get_broker_tz_offset(), get_mt5_atr(), get_mt5_indicators(), get_mt5_live_price(), get_mt5_stock_data(), get_mt5_ticks() (+31 more)

### Community 6 - "Community 6"
Cohesion: 0.07
Nodes (17): CandleBuilder, datetime, Dedicated thread that polls MT5 for ticks and feeds candle builders.      Call, Expose tick_buffer as a list., Calculate order imbalance across 10s, 60s, and 300s windows., Fetch new ticks since last known tick time., Builds OHLCV candles from raw ticks for a single timeframe., Update bid/ask, feed candle builders, invoke callbacks. (+9 more)

### Community 7 - "Community 7"
Cohesion: 0.12
Nodes (23): AxonAI real-time trading daemon.  Always-alive process that monitors MT5 tick, Pure Python event detection engine.  Watches the live state for structural mar, EventPriority, EventType, MarketEvent, Event type definitions for the real-time trading engine., A detected market event that may trigger graph execution., axonai.realtime – Real-time trading engine components. (+15 more)

### Community 8 - "Community 8"
Cohesion: 0.09
Nodes (30): generate_session_summary(), Read reports/dry_run_session.jsonl and print a formatted summary., lookup(), MatrixAction, Velocity-Displacement Matrix Lookup.  Pure function that maps velocity percentil, Actions from velocity-displacement matrix lookup., Matrix lookup: velocity × displacement × location → action.      Args:         v, backtest() (+22 more)

### Community 9 - "Community 9"
Cohesion: 0.08
Nodes (19): get_dst_session_hours(), LiveMarketEvidence, datetime, Incrementally updated live market state.  Seeds from historical MT5 bars once at, Reset and recalculate all institutional levels daily using local history., Reset PWH/PWL weekly using local history., Update indicators and handle level invalidation on candle close., Scan self._m15_candles to find local swing highs and swing lows (window of 3) as (+11 more)

### Community 10 - "Community 10"
Cohesion: 0.08
Nodes (18): datetime, Lazily create/return the per-session baseline bucket.          Loaded buckets re, Process one tick and return normalized velocity state.          Args:, Reset session baseline (call on session boundary)., Reset the peak velocity tracking (call when entering a trade)., Ticks per second over the last `window_sec` seconds., Compute net and absolute velocity in pips/sec over the velocity window., Ratio of net displacement to total path (0 = chop, 1 = impulse). (+10 more)

### Community 11 - "Community 11"
Cohesion: 0.09
Nodes (19): FakeDisplacement, FakeLocationContext, FakeSnapshot, FakeVelocity, datetime, Trade State Engine: Lifecycle Phase Tracking.  Tracks trade lifecycle phases (, Register a new trade with the state engine., Update trade state every tick.          Args:             price: Current pric (+11 more)

### Community 12 - "Community 12"
Cohesion: 0.12
Nodes (14): get_dashboard(), Get the active global dashboard server instance., AxonDaemon, datetime, Warm MTF EMAs, regime, and daily levels from historical bars before         the, Called by TickEngine when any timeframe candle closes., Persistently log every generated signal to reports/signals.jsonl and reports/sig, Compute active/inactive state + progress for each forex session with dynamic DST (+6 more)

### Community 13 - "Community 13"
Cohesion: 0.12
Nodes (16): BacktestEngine, Any, datetime, Generate realistic multi-phase synthetic market dataset.                  Market, Pre-populate the H1 and H4 candle deques from the beginning of historical data t, Historical data simulator and backtesting engine for AxonAI., Execute the backtest simulation sequentially, detecting events and managing trad, Evaluate engine decisions with backtester specific constraints. (+8 more)

### Community 14 - "Community 14"
Cohesion: 0.10
Nodes (15): Classify regime on a new candle close.          Args:             candle: Just-c, Update EMAs, ATR, Bollinger width on candle close., Strong directional move: EMA alignment + velocity acceleration + high displaceme, Existing trend + moderate velocity., No trend + low displacement., Declining volatility + squeeze., Price beyond range + velocity spike + high displacement., Velocity decaying, displacement divergence. (+7 more)

### Community 15 - "Community 15"
Cohesion: 0.10
Nodes (17): DisplacementNormalizer, NormalizedDisplacement, Displacement ratio normalization layer.  Converts raw displacement ratios into z, Clear rolling window (call on session boundary)., Output snapshot of displacement normalization on every tick., Computes z-scores for displacement ratios over rolling time windows.      Design, Args:             window_sec: Rolling time window in seconds (default 300 = 5 mi, Process one displacement ratio and return normalized state.          Args: (+9 more)

### Community 16 - "Community 16"
Cohesion: 0.10
Nodes (15): Test smart dynamic cooldown logic.  Smart cooldown adjusts based on trade outcom, After cooldown expires, _seconds_until_ready returns 0., With multiple active positions, uses their collective state., Scenario: Trade loses 5 pips, next signal within 20 sec should be allowed., Scenario: Trade wins +5 pips, cooldown should be long (120s)., Test dynamic cooldown based on trade profit/loss., Create a mock daemon with trade state., No active trade → 30 second fast recovery cooldown. (+7 more)

### Community 17 - "Community 17"
Cohesion: 0.09
Nodes (19): _apply_env_overrides(), _coerce(), Coerce env-var string to the type of the existing default value., Apply AXONAI_* env vars to the config dict in-place., DataflowsConfigIsolationTests, Config isolation: get/set must not leak nested-dict references., Tests for AXONAI_* env-var overlay onto DEFAULT_CONFIG., Set/clear env vars then reload default_config to re-evaluate DEFAULT_CONFIG. (+11 more)

### Community 18 - "Community 18"
Cohesion: 0.15
Nodes (24): broadcast(), data_loop(), ensure_symbol(), get_account_data(), get_candles_data(), get_historical_bars(), get_levels_data(), get_regime_data() (+16 more)

### Community 19 - "Community 19"
Cohesion: 0.19
Nodes (20): _direction_from(), EntryDecision, evaluate_peak_entry(), Shared entry-quality gate for microstructure-peak reversals.  Single source of t, Map an event direction string to a trade side (fail closed)., Decide whether a PEAK_DETECTION event qualifies for entry.      Returns an Entry, _event(), _gate() (+12 more)

### Community 20 - "Community 20"
Cohesion: 0.11
Nodes (11): BridgeClient, Receive and relay messages from the bridge., Send an arbitrary JSON message to the bridge., Send a request for historical data through the bridge., Send a message via WebSocket., Check if connected to the bridge., Connects to the Windows MT5 Bridge and relays data to the dashboard.      The br, Start the bridge client in a background thread. (+3 more)

### Community 21 - "Community 21"
Cohesion: 0.18
Nodes (11): When displacement shows absorption/trap, transition ANOMALY → ARMING., When in ARMING and price breaks away with impulse, transition to TRIGGERED., Full state machine cycle in quiet market: IDLE → ANOMALY → ARMING → TRIGGERED., At exactly 36th percentile, velocity is NOT unusual yet., Above 36th percentile WITH is_unusual=True triggers anomaly., Create a mock NormalizedVelocity., Create a mock DisplacementState., Create a mock LiquidityState. (+3 more)

### Community 22 - "Community 22"
Cohesion: 0.13
Nodes (10): LiveWorldState, Cold start: build full WorldState from historical bars.         Called once when, Update indicators when a candle closes., Update ATR, EMA, RSI on H1 candle close., Update H4 EMA on H4 candle close., Recompute regime scores on M15 candle close., Lightweight volume update on M5., Recompute belief score and gate decision with dynamic thresholds. (+2 more)

### Community 23 - "Community 23"
Cohesion: 0.17
Nodes (7): DisplacementState, Output snapshot of displacement analysis on every tick., Detect if stops are being hunted/manipulated.          Returns: (stop_hunt_detec, Estimate how many ticks the reversal signal is lagged.          A lagged reversa, PhaseSnapshot, Tracks trade lifecycle phase and confidence score.      Call register_trade() on, TradePhaseTracker

### Community 24 - "Community 24"
Cohesion: 0.16
Nodes (8): PeakDetector, PeakSignal, datetime, Peak and climax exhaustion detection engine with advanced tick microstructure me, Detects price exhaustion peaks, volume climaxes, price-per-tick efficiency colla, Update indicators with new tick data and check for peaks.                  Retur, Live tick behavior analysis — velocity, imbalance, microstructure peaks, level i, TestPeakDetector

### Community 25 - "Community 25"
Cohesion: 0.14
Nodes (11): Should skip entry when reversal_confidence is too low., Should size position lower when reversal is MODERATE vs STRONG., Should ALLOW entry when stop hunt is REVERSING (confirmed)., Signal quality should reflect consensus strength., Should show urgency when entry window is closing., Create a mock MarketContext for testing., Test EntryStateMachine using MarketContext quality scores., Should not enter when stop hunting is detected (fake reversal). (+3 more)

### Community 26 - "Community 26"
Cohesion: 0.11
Nodes (10): LevelBehaviorTracker, Return current level behavior state for serialization into MarketEvidence., Direct access for EventDetector queries., Quick check: is price absorbing at this level?, How many times has this level been attacked?, How many consecutive approaches without full pullback?, Remove behaviors for levels no longer active or not seen for a while., Clear all tracked behavior state. (+2 more)

### Community 27 - "Community 27"
Cohesion: 0.14
Nodes (11): Process one tick through all detectors.          Args:             bid: Current, Return current rolling statistics as a dict (for dashboard / debug)., Return level behavior summary., Flush and close the current log file., Append one JSON line to the rolling log file., Remove oldest log files beyond max_log_files., Serialisable snapshot of tick behaviour at a point in time., Processes live ticks through multiple detectors and logs results.      Usage: (+3 more)

### Community 28 - "Community 28"
Cohesion: 0.18
Nodes (15): get_account_info_via_bridge(), _get_bridge_script(), get_positions_via_bridge(), Any, MT5 Order Bridge for dual-terminal architecture.  Maintains data feed on Exness, Start the order bridge subprocess., Send a command through the bridge subprocess., Send an order through the bridge subprocess.      Args:         mt5_trade_termin (+7 more)

### Community 29 - "Community 29"
Cohesion: 0.17
Nodes (8): NewsGuard, datetime, Dynamic News Guard — blocks new entries around high-impact economic news.  Pair-, Parse ForexFactory weekly JSON rows into event dicts (keeps prev/forecast/actual, EURUSD / EURUSDm / EURUSD=X → {"EUR", "USD"}., Return (blocked, reason). Blocked if a relevant event is inside the         [pre, Pair- and impact-aware economic-news blackout filter., Fetch the calendar if stale; fall back to disk cache. Returns count.

### Community 30 - "Community 30"
Cohesion: 0.15
Nodes (9): Detect if price tested SL area (within window pips) and bounced back up., Calculate aggressiveness based on LIVE conditions, not fixed thresholds., Momentum-state multiplier in [0.4, 2.5].          High (give the move room) when, Pips to keep behind price, driven by momentum state.          width_mult (from m, Clear trail state when position closes., Real-time velocity trailing with dynamic market buffer adaptation.      Key insi, Reference-ratio scale for pip-distance constants.          effective = original, Real-time velocity trailing with retest detection and dynamic market buffer. (+1 more)

### Community 31 - "Community 31"
Cohesion: 0.12
Nodes (9): Test suite for MT5TradeExecutor with mocked MT5 interface., Test that HOLD signals return None and make no calls., Test that dryrun config overrides lot size to 1.00 and returns sl., Paper-trade mode returns a simulated fill and NEVER calls order_send., Successive paper fills get distinct, incrementing synthetic tickets., A spread wider than max_spread_frac of the stop rejects the entry., Test BUY signal order composition and execution., Test SELL signal order composition and execution. (+1 more)

### Community 32 - "Community 32"
Cohesion: 0.14
Nodes (11): ExitSignal, FakeDisplacement, FakeLegacyExit, FakeLocationContext, FakeSnapshot, FakeTradeState, FakeVelocity, Exit Engine: Priority-Based Trade Exit Logic.  Evaluates exit conditions using (+3 more)

### Community 33 - "Community 33"
Cohesion: 0.21
Nodes (9): LevelBehavior, datetime, Tick-level behavior tracking around price levels.  Monitors how price interacts, Process one tick across all active levels. O(n) where n = levels., Per-level real-time tick behavior tracking state., Initialize a new approach tracking cycle., Enforce max approach duration., Close an active approach and classify outcome. (+1 more)

### Community 34 - "Community 34"
Cohesion: 0.19
Nodes (9): BridgeDataCollector, build_monthly_breakdown(), main(), Convert bridge bars to a DataFrame matching BacktestEngine expectations., Populate engine.live_evidence.price_levels from candle data.          The engine, Convert bridge bars to (LiveCandle list, interpolated tick list)., Group trades by calendar month and compute WR + PF per month., Async WebSocket client that fetches historical candles in chunks. (+1 more)

### Community 35 - "Community 35"
Cohesion: 0.15
Nodes (10): FastAPI WebSocket server for real-time visual signaling dashboard.  Integrates, Launch the API and web server in a daemon thread., Helper to initialize and start the global dashboard server., start_dashboard(), MT5 Bridge Client — runs in WSL, connects to the Windows MT5 Bridge Service and, on_tick_from_bridge(), Start the dashboard with MT5 bridge client connected to Windows bridge., Bridge tick callback → feed into analyzer and broadcast enriched data. (+2 more)

### Community 36 - "Community 36"
Cohesion: 0.18
Nodes (9): Manage velocity-based trailing stops on active MT5 positions., Send an order via the bridge subprocess., Flatten all positions on the active → wind-down session transition.          F, Close every open position for this symbol/magic. Returns count closed., Send command to execution_bridge.py and return the response., Helper to run a coroutine in both synchronous and asynchronous contexts safely., run_coroutine(), send_execution_command() (+1 more)

### Community 37 - "Community 37"
Cohesion: 0.18
Nodes (10): LevelState, LiquidityEngine, LiquidityEvent, datetime, Process one tick against all active levels., Determine if an active breach is a sweep or a structural break., Represents a specific interaction with a price level., Live state of a single price level. (+2 more)

### Community 38 - "Community 38"
Cohesion: 0.17
Nodes (7): Shut down the order bridge subprocess., stop_bridge(), Main thread: blocks on event queue, fires graph on valid events., Smart dynamic cooldown based on trade outcome and direction., Log daemon statistics., Handle SIGINT/SIGTERM for graceful shutdown., Append an event to the dry run session log.

### Community 39 - "Community 39"
Cohesion: 0.21
Nodes (6): Helper checking if circuit breaker has tripped., Drawdown Circuit Breaker.          Tracks daily profit/loss and halts all execut, Seed daily starting equity on first call of the day., Update realized PnL for the day., Check if circuit breaker has tripped., RiskGuard

### Community 40 - "Community 40"
Cohesion: 0.20
Nodes (7): DashboardServer, Manages the FastAPI lifecycle and WebSocket broadcasts., Send all cached state history to a newly connected client., Load session state from disk on startup., Bind endpoints to FastAPI app., Target for Uvicorn runner inside the thread., WebSocket

### Community 41 - "Community 41"
Cohesion: 0.24
Nodes (7): MT5TradeExecutor, Any, Handles sending order requests to MetaTrader 5 (either directly or via Execution, Helper to convert OrderSendResult to a dictionary., Get MT5 instance: prefer trade terminal (dual-terminal), fall back to main MT5., Convert a 5-tier signal into an MT5 order action.          Signals: Buy, Overw, Send a market order with dynamic SL/TP and position sizing to MT5.

### Community 42 - "Community 42"
Cohesion: 0.22
Nodes (7): convert_numpy(), Any, Recursively convert numpy types to native Python types for JSON serialization., Thread-safe queueing of message broadcast across all websockets., Asynchronously send message to all sockets., Save event history, latest decision, and levels state to disk., Background thread: periodically fetches the economic calendar from NewsGuard.

### Community 43 - "Community 43"
Cohesion: 0.18
Nodes (3): Tests for the ticker path-component validator that blocks directory traversal., Sanity: sanitized values stay within base when joined., TestSafeTickerComponent

### Community 44 - "Community 44"
Cohesion: 0.29
Nodes (3): ExitRecord, ExitStats, Exit Statistics Collector.  Records every exit event with reason, pips, phase, c

### Community 45 - "Community 45"
Cohesion: 0.27
Nodes (6): Test MarketContext immutability and structure., MarketContext dataclass must be frozen (immutable)., MarketContext includes all 6 math engine outputs., Summary should be human-readable., Create a basic MarketContext for testing., TestMarketContext

### Community 46 - "Community 46"
Cohesion: 0.27
Nodes (8): handle_client(), main(), mt5_init(), Initialize MT5 connection to the target execution terminal., Convert MT5 position object to dict., Handle connection from daemon client., run_http_server(), serialize_position()

### Community 47 - "Community 47"
Cohesion: 0.28
Nodes (5): MTFContext, Calculate a -1.0 to 1.0 bias score for a specific timeframe., Computes multi-timeframe alignment scores using EMAs and structure., Update state when a candle closes on any timeframe., Calculate the current multi-timeframe alignment.

### Community 48 - "Community 48"
Cohesion: 0.25
Nodes (5): get_mt5_trade(), Get the MT5 instance for trade execution., Risk management and drawdown circuit breaker for AxonAI.  Monitors daily profit/, Trade execution module for MetaTrader 5.  Performs live order routing, positio, Unit and integration tests for MT5TradeExecutor.

### Community 49 - "Community 49"
Cohesion: 0.43
Nodes (6): Map a peak event to a trade side. Returns 'BUY', 'SELL', or None.          Sin, _evt(), Unit tests for AxonDaemon._entry_direction — the single source of truth for mapp, test_bearish_maps_to_sell(), test_bullish_maps_to_buy(), test_indeterminate_fails_closed()

### Community 50 - "Community 50"
Cohesion: 0.29
Nodes (4): Complete the trade record and write to disk., Complete lifecycle record of a single trade., Create a new trade record with pre-trade context., TradeRecord

### Community 51 - "Community 51"
Cohesion: 0.29
Nodes (5): display_announcements(), fetch_announcements(), Fetch announcements from endpoint. Returns dict with announcements and settings., Display announcements panel. Prompts for Enter if require_attention is True., Console

### Community 52 - "Community 52"
Cohesion: 0.33
Nodes (4): Notification and alert dispatch module for AxonAI., Dispatch an alert message to configured destinations (Telegram, Webhook)., send_alert(), Simulate an instant fill at the requested price (paper-trade mode).

### Community 53 - "Community 53"
Cohesion: 0.33
Nodes (3): Trade Analytics Tracker.  Captures comprehensive pre-trade context and post-trad, Records trade history for off-line evaluation., TradeAnalytics

### Community 54 - "Community 54"
Cohesion: 0.33
Nodes (4): patched_fetch_bars(), patched_load_historical_data(), Return our pre-built candles instead of calling MT5., Return pre-built candles and ticks, bypassing MT5 entirely.

### Community 57 - "Community 57"
Cohesion: 0.50
Nodes (3): Velocity spike detection in RANGE_CHOP (quiet) market., Machine starts in IDLE state., TestVelocitySpikeQuietMarket

## Knowledge Gaps
- **11 isolated node(s):** `FakeVelocity`, `FakeDisplacement`, `FakeSnapshot`, `FakeTradeState`, `FakeLocationContext` (+6 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `LiveCandle` connect `Community 4` to `Community 0`, `Community 1`, `Community 34`, `Community 3`, `Community 5`, `Community 6`, `Community 7`, `Community 9`, `Community 12`, `Community 13`, `Community 14`, `Community 47`, `Community 22`, `Community 55`, `Community 54`?**
  _High betweenness centrality (0.180) - this node is a cross-community bridge._
- **Why does `AxonDaemon` connect `Community 12` to `Community 0`, `Community 36`, `Community 4`, `Community 38`, `Community 7`, `Community 6`, `Community 9`, `Community 41`, `Community 8`, `Community 15`, `Community 16`, `Community 49`, `Community 53`, `Community 22`, `Community 28`, `Community 29`, `Community 30`?**
  _High betweenness centrality (0.159) - this node is a cross-community bridge._
- **Why does `ReversalModel` connect `Community 0` to `Community 1`, `Community 3`, `Community 4`, `Community 5`, `Community 37`, `Community 7`, `Community 10`, `Community 11`, `Community 12`, `Community 13`, `Community 44`, `Community 47`, `Community 14`, `Community 53`, `Community 23`?**
  _High betweenness centrality (0.111) - this node is a cross-community bridge._
- **Are the 29 inferred relationships involving `NormalizedVelocity` (e.g. with `AdaptiveExitManager` and `ExitDecision`) actually correct?**
  _`NormalizedVelocity` has 29 INFERRED edges - model-reasoned connections that need verification._
- **Are the 28 inferred relationships involving `DisplacementState` (e.g. with `AdaptiveExitManager` and `ExitDecision`) actually correct?**
  _`DisplacementState` has 28 INFERRED edges - model-reasoned connections that need verification._
- **Are the 16 inferred relationships involving `LiveCandle` (e.g. with `BacktestEngine` and `AxonDaemon`) actually correct?**
  _`LiveCandle` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 26 inferred relationships involving `RegimeState` (e.g. with `AdaptiveExitManager` and `ExitDecision`) actually correct?**
  _`RegimeState` has 26 INFERRED edges - model-reasoned connections that need verification._