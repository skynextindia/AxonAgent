# Graph Report - AxonAgent-Agy  (2026-07-11)

## Corpus Check
- 93 files · ~103,272 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1424 nodes · 3115 edges · 73 communities (65 shown, 8 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 315 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `6480883c`
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
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 73|Community 73]]

## God Nodes (most connected - your core abstractions)
1. `LiveCandle` - 86 edges
2. `NormalizedVelocity` - 85 edges
3. `DisplacementState` - 74 edges
4. `AxonDaemon` - 57 edges
5. `RegimeState` - 53 edges
6. `ReversalModel` - 46 edges
7. `LiquidityState` - 43 edges
8. `EntryStateMachine` - 41 edges
9. `EventDetector` - 41 edges
10. `MarketContextBuilder` - 40 edges

## Surprising Connections (you probably didn't know these)
- `BridgeDataCollector` --uses--> `BacktestEngine`  [INFERRED]
  run_bridge_backtest.py → axonai/realtime/backtester.py
- `ColorFormatter` --uses--> `AxonDaemon`  [INFERRED]
  run.py → axonai/realtime/daemon.py
- `TestDaemonE2E` --uses--> `AxonDaemon`  [INFERRED]
  tests/test_daemon_e2e.py → axonai/realtime/daemon.py
- `TestSmartCooldown` --uses--> `AxonDaemon`  [INFERRED]
  tests/test_smart_cooldown.py → axonai/realtime/daemon.py
- `TestEntryStateMachineWithMarketContext` --uses--> `DisplacementState`  [INFERRED]
  tests/test_entry_state_machine_with_market_context.py → axonai/realtime/displacement_engine.py

## Import Cycles
- None detected.

## Communities (73 total, 8 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.08
Nodes (41): AdaptiveExitManager, ExitDecision, Adaptive Exit Engine.  Replaces fixed ATR TP/SL. Uses the Trade Health Monitor a, Evaluate if we should hold, adjust SL/TP, or force close., Output of the exit evaluation., Manages active trades, adjusting targets or cutting losses dynamically., Register the trade to track., DisplacementState (+33 more)

### Community 1 - "Community 1"
Cohesion: 0.08
Nodes (20): _candle_body_ratio(), CandleSetup, CandleSetupTracker, _is_engulfing(), _is_pin_bar(), _PendingSweep, datetime, Candle Setup Tracker.  Detects high-probability reversal setups from M15/H1 cand (+12 more)

### Community 2 - "Community 2"
Cohesion: 0.09
Nodes (24): MarketContextBuilder, Build MarketContext with quality scores from engine outputs., Determine overall market context verdict.          Matrix:         - confidence, Is the reversal opportunity closing/expiring?          Windows close when:, When some engines agree, score should reflect the ratio., When no engines agree, score should be low., High velocity + impulse displacement + at major level = high confidence., Low velocity + neutral displacement + random location = low confidence. (+16 more)

### Community 3 - "Community 3"
Cohesion: 0.07
Nodes (37): DisplacementBufferEngine, DynamicDisplacementThresholds, Dynamic Displacement Buffer Engine for Adaptive Entry Thresholds.  Replaces stat, Compute dynamic displacement thresholds from regime.          Args:, Output: dynamic entry thresholds based on market regime., Computes dynamic displacement thresholds from market regime.      Adapts impulse, Args:             config: Optional config dict with:               - impulse_rat, DisplacementEngine (+29 more)

### Community 4 - "Community 4"
Cohesion: 0.07
Nodes (28): EventDetector, datetime, Add trigger candle details to the event details if available., Set pip multiplier based on pair type., Lightweight per-tick checks., Invoke microstructure peak and climax exhaustion detector for both systems., Structural checks on candle close., Detects structural market events from live state.      Detection rules (all pu (+20 more)

### Community 5 - "Community 5"
Cohesion: 0.07
Nodes (50): extract_market_evidence(), MarketEvidence, Extract structured facts from raw MT5 data in pure Python. Fail safe if MT5 is u, _ensure_symbol_visible(), _fetch_bars(), get_broker_tz_offset(), get_mt5_atr(), get_mt5_indicators() (+42 more)

### Community 6 - "Community 6"
Cohesion: 0.06
Nodes (21): CandleBuilder, datetime, Dedicated thread that polls MT5 for ticks and feeds candle builders.      Call, Expose tick_buffer as a list., Calculate order imbalance across 10s, 60s, and 300s windows., Lazy-load and initialize MT5., Pre-seed active incomplete candles from MT5., Fetch new ticks since last known tick time. (+13 more)

### Community 7 - "Community 7"
Cohesion: 0.33
Nodes (5): datetime, Process one tick across all active levels. O(n) where n = levels., Initialize a new approach tracking cycle., Enforce max approach duration., Close an active approach and classify outcome.

### Community 8 - "Community 8"
Cohesion: 0.09
Nodes (30): generate_session_summary(), Read reports/dry_run_session.jsonl and print a formatted summary., lookup(), MatrixAction, Velocity-Displacement Matrix Lookup.  Pure function that maps velocity percentil, Actions from velocity-displacement matrix lookup., Matrix lookup: velocity × displacement × location → action.      Args:         v, backtest() (+22 more)

### Community 9 - "Community 9"
Cohesion: 0.08
Nodes (20): get_dst_session_hours(), LiveMarketEvidence, PriceLevel, datetime, Reset and recalculate all institutional levels daily using local history., Reset PWH/PWL weekly using local history., Update indicators and handle level invalidation on candle close., Scan self._m15_candles to find local swing highs and swing lows (window of 3) as (+12 more)

### Community 10 - "Community 10"
Cohesion: 0.06
Nodes (25): datetime, Lazily create/return the per-session baseline bucket.          Loaded buckets re, Process one tick and return normalized velocity state.          Args:, Reset session baseline (call on session boundary)., Reset the peak velocity tracking (call when entering a trade)., Ticks per second over the last `window_sec` seconds., Compute net and absolute velocity in pips/sec over the velocity window., Ratio of net displacement to total path (0 = chop, 1 = impulse). (+17 more)

### Community 11 - "Community 11"
Cohesion: 0.09
Nodes (19): FakeDisplacement, FakeLocationContext, FakeSnapshot, FakeVelocity, datetime, Trade State Engine: Lifecycle Phase Tracking.  Tracks trade lifecycle phases (, Register a new trade with the state engine., Update trade state every tick.          Args:             price: Current pric (+11 more)

### Community 12 - "Community 12"
Cohesion: 0.15
Nodes (19): get_account_info_via_bridge(), _get_bridge_script(), get_orders_via_bridge(), get_positions_via_bridge(), Any, MT5 Order Bridge for dual-terminal architecture.  Maintains data feed on Exness, Start the order bridge subprocess., Send a command through the bridge subprocess. (+11 more)

### Community 13 - "Community 13"
Cohesion: 0.10
Nodes (18): BacktestEngine, Any, datetime, Generate realistic multi-phase synthetic market dataset.                  Market, Pre-populate the H1 and H4 candle deques from the beginning of historical data t, Historical data simulator and backtesting engine for AxonAI., Execute the backtest simulation sequentially, detecting events and managing trad, Fetch real data from MT5 if connected, otherwise generate synthetic market datas (+10 more)

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
Cohesion: 0.06
Nodes (28): convert_numpy(), DashboardServer, Any, Target for Uvicorn runner inside the thread., Retrieve or create a thread lock specific to a symbol to reduce contention., Register a daemon instance for multicurrency support., Bind endpoints to FastAPI app., Recursively convert numpy types to native Python types for JSON serialization. (+20 more)

### Community 18 - "Community 18"
Cohesion: 0.15
Nodes (24): broadcast(), data_loop(), ensure_symbol(), get_account_data(), get_candles_data(), get_historical_bars(), get_levels_data(), get_regime_data() (+16 more)

### Community 19 - "Community 19"
Cohesion: 0.19
Nodes (20): _direction_from(), EntryDecision, evaluate_peak_entry(), Shared entry-quality gate for microstructure-peak reversals.  Single source of t, Map an event direction string to a trade side (fail closed)., Decide whether a PEAK_DETECTION event qualifies for entry.      Returns an Entry, _event(), _gate() (+12 more)

### Community 20 - "Community 20"
Cohesion: 0.12
Nodes (9): BridgeClient, MT5 Bridge Client — runs in WSL, connects to the Windows MT5 Bridge Service and, Receive and relay messages from the bridge., Check if connected to the bridge., Connects to the Windows MT5 Bridge and relays data to the dashboard.      The br, Start the bridge client in a background thread., Stop the bridge client., Run the asyncio event loop in a background thread. (+1 more)

### Community 21 - "Community 21"
Cohesion: 0.07
Nodes (29): EntryDecision, EntryStateMachine, datetime, Force the machine back to IDLE., Evaluate conditions and transition states., Evaluate entry decision using MarketContext quality scores.          Uses qual, Look for the initial anomaly (Microstructure Peak).          With the candle s, Wait for the anomaly to form a trap or show absorption. (+21 more)

### Community 22 - "Community 22"
Cohesion: 0.15
Nodes (9): LiveWorldState, Update indicators when a candle closes., Update ATR, EMA, RSI on H1 candle close., Update H4 EMA on H4 candle close., Recompute regime scores on M15 candle close., Lightweight volume update on M5., Recompute belief score and gate decision with dynamic thresholds., Update base and quote currency strength dynamically using cross-pair correlation (+1 more)

### Community 23 - "Community 23"
Cohesion: 0.09
Nodes (29): FastAPI WebSocket server for real-time visual signaling dashboard.  Integrates, High-fidelity historical backtesting engine with candle, peak, reversal, and swe, AxonAI real-time trading daemon.  Always-alive process that monitors MT5 tick, Pure Python event detection engine.  Watches the live state for structural mar, EventPriority, EventType, MarketEvent, Event type definitions for the real-time trading engine. (+21 more)

### Community 24 - "Community 24"
Cohesion: 0.16
Nodes (8): PeakDetector, PeakSignal, datetime, Peak and climax exhaustion detection engine with advanced tick microstructure me, Detects price exhaustion peaks, volume climaxes, price-per-tick efficiency colla, Update indicators with new tick data and check for peaks.                  Retur, Live tick behavior analysis — velocity, imbalance, microstructure peaks, level i, TestPeakDetector

### Community 25 - "Community 25"
Cohesion: 0.18
Nodes (11): When displacement shows absorption/trap, transition ANOMALY → ARMING., When in ARMING and price breaks away with impulse, transition to TRIGGERED., Full state machine cycle in quiet market: IDLE → ANOMALY → ARMING → RETEST_WAIT, At exactly 36th percentile, velocity is NOT unusual yet., Above 36th percentile WITH is_unusual=True triggers anomaly., Create a mock NormalizedVelocity., Create a mock DisplacementState., Create a mock LiquidityState. (+3 more)

### Community 26 - "Community 26"
Cohesion: 0.10
Nodes (11): LevelBehaviorTracker, Return current level behavior state for serialization into MarketEvidence., Direct access for EventDetector queries., Quick check: is price absorbing at this level?, How many times has this level been attacked?, How many consecutive approaches without full pullback?, Remove behaviors for levels no longer active or not seen for a while., Clear all tracked behavior state. (+3 more)

### Community 27 - "Community 27"
Cohesion: 0.10
Nodes (23): get_config(), initialize_config(), Initialize the configuration with default values., Update the configuration with custom values.      Dict-valued keys (e.g. ``dat, Get the current configuration., set_config(), _apply_env_overrides(), _coerce() (+15 more)

### Community 28 - "Community 28"
Cohesion: 0.08
Nodes (18): LocationEngine, Computes market location (distance to levels, at_structure flag)., Args:             pip_mult: 0.0001 for majors, 0.01 for JPY pairs             co, Compute market location metrics.          Args:             price: Current bid/a, MTFContext, Calculate the current multi-timeframe alignment., Calculate a -1.0 to 1.0 bias score for a specific timeframe., Computes multi-timeframe alignment scores using EMAs and structure. (+10 more)

### Community 29 - "Community 29"
Cohesion: 0.29
Nodes (3): ExitRecord, ExitStats, Exit Statistics Collector.  Records every exit event with reason, pips, phase, c

### Community 30 - "Community 30"
Cohesion: 0.40
Nodes (3): datetime, Process one tick and return displacement state.          Args:             price, Multi-factor displacement classification.          Decision matrix:           Hi

### Community 31 - "Community 31"
Cohesion: 0.09
Nodes (12): Unit and integration tests for MT5TradeExecutor., Test suite for MT5TradeExecutor with mocked MT5 interface., Test that HOLD signals return None and make no calls., Test that dryrun config overrides lot size to 1.00 and returns sl., Paper-trade mode returns a simulated fill and NEVER calls order_send., Successive paper fills get distinct, incrementing synthetic tickets., A spread wider than max_spread_frac of the stop rejects the entry., Test BUY signal order composition and execution. (+4 more)

### Community 32 - "Community 32"
Cohesion: 0.14
Nodes (11): ExitSignal, FakeDisplacement, FakeLegacyExit, FakeLocationContext, FakeSnapshot, FakeTradeState, FakeVelocity, Exit Engine: Priority-Based Trade Exit Logic.  Evaluates exit conditions using (+3 more)

### Community 33 - "Community 33"
Cohesion: 0.18
Nodes (7): Intraday dynamic liquidity pools created by velocity spikes., Register a new velocity event level or update an existing close level., Check if current price is within the zone of an active level for a retest., Invalidate levels that have been cleanly broken (beyond-extreme)., Clear the registry (call daily/on reset)., VelocityLevel, VelocityLevelRegistry

### Community 34 - "Community 34"
Cohesion: 0.22
Nodes (5): DataFrame, Validate ``value`` is safe to interpolate into a filesystem path.      Tickers, safe_ticker_component(), save_output(), SavePathType

### Community 35 - "Community 35"
Cohesion: 0.21
Nodes (7): get_dashboard(), Get the active global dashboard server instance., datetime, Called by TickEngine on every new tick., Called by TickEngine when any timeframe candle closes., Append the daemon-processed engine snapshot to a per-pair CSV store., Compute active/inactive state + progress for each forex session with dynamic DST

### Community 36 - "Community 36"
Cohesion: 0.12
Nodes (13): AxonDaemon, Warm MTF EMAs, regime, and daily levels from historical bars before         the, Main thread: blocks on event queue, fires graph on valid events., Smart dynamic level-aware and direction-aware cooldown check., Log daemon statistics., Handle SIGINT/SIGTERM for graceful shutdown., Append an event to the dry run session log., Manage velocity-based trailing stops on active MT5 positions. (+5 more)

### Community 37 - "Community 37"
Cohesion: 0.27
Nodes (3): Persistently log every generated signal to reports/signals.jsonl and reports/sig, Detect closed positions and log outcomes., SymbolColorLogger

### Community 39 - "Community 39"
Cohesion: 0.16
Nodes (7): Risk management and drawdown circuit breaker for AxonAI.  Monitors daily profit/, Helper checking if circuit breaker has tripped., Drawdown Circuit Breaker.          Tracks daily profit/loss and halts all execut, Seed daily starting equity on first call of the day., Update realized PnL for the day., Check if circuit breaker has tripped., RiskGuard

### Community 40 - "Community 40"
Cohesion: 0.13
Nodes (8): datetime, Calculate what % of engines agree on reversal.          Returns: (agreement_scor, Score 0-100: How clear/strong is the reversal signal?          Multi-factor scor, Detect if stops are being hunted/manipulated.          Returns: (stop_hunt_detec, Estimate how many ticks the reversal signal is lagged.          A lagged reversa, Track how long displacement has been in current classification., How many ticks until this signal becomes stale/invalid?          Signals expire, Assemble MarketContext with all quality scores calculated.

### Community 41 - "Community 41"
Cohesion: 0.15
Nodes (12): Notification and alert dispatch module for AxonAI., Dispatch an alert message to configured destinations (Telegram, Webhook)., send_alert(), MT5TradeExecutor, Any, Handles sending order requests to MetaTrader 5 (either directly or via Execution, Simulate an instant fill at the requested price (paper-trade mode)., Helper to convert OrderSendResult to a dictionary. (+4 more)

### Community 42 - "Community 42"
Cohesion: 0.43
Nodes (6): Map a peak event to a trade side. Returns 'BUY', 'SELL', or None.          Sin, _evt(), Unit tests for AxonDaemon._entry_direction — the single source of truth for mapp, test_bearish_maps_to_sell(), test_bullish_maps_to_buy(), test_indeterminate_fails_closed()

### Community 43 - "Community 43"
Cohesion: 0.14
Nodes (11): Process one tick through all detectors.          Args:             bid: Current, Return current rolling statistics as a dict (for dashboard / debug)., Return level behavior summary., Flush and close the current log file., Append one JSON line to the rolling log file., Remove oldest log files beyond max_log_files., Serialisable snapshot of tick behaviour at a point in time., Processes live ticks through multiple detectors and logs results.      Usage: (+3 more)

### Community 44 - "Community 44"
Cohesion: 0.42
Nodes (13): _disp(), _liq(), _lvl(), _mtf(), Tests for per-pair scaling + graded fail-open reversal gate (WS1/WS4)., test_counter_trend_with_reversal_pressure_allowed(), test_counter_trend_without_exhaustion_rejected(), test_displacement_trend_threshold_is_pair_scaled() (+5 more)

### Community 45 - "Community 45"
Cohesion: 0.27
Nodes (6): Test MarketContext immutability and structure., MarketContext dataclass must be frozen (immutable)., MarketContext includes all 6 math engine outputs., Summary should be human-readable., Create a basic MarketContext for testing., TestMarketContext

### Community 46 - "Community 46"
Cohesion: 0.24
Nodes (10): handle_client(), main(), mt5_init(), Handle connection from daemon client., Initialize MT5 connection to the target execution terminal., Send order with auto-reinitialize retry on IPC send failed errors., Convert MT5 position object to dict., run_http_server() (+2 more)

### Community 47 - "Community 47"
Cohesion: 0.50
Nodes (3): Helper to initialize and start the global dashboard server., Launch the API and web server in a daemon thread., start_dashboard()

### Community 48 - "Community 48"
Cohesion: 0.50
Nodes (3): get_mt5_trade(), Get the MT5 instance for trade execution., Trade execution module for MetaTrader 5.  Performs live order routing, positio

### Community 49 - "Community 49"
Cohesion: 0.20
Nodes (14): Set the global fallback path for the feed terminal (e.g. Exness).      CRITICA, set_feed_terminal_path(), enable_ansi_escape_sequences(), get_windows_host_ip(), is_windows(), is_wsl(), main(), Launch the Deep Scan Calibrator in continuous mode for a specific symbol. (+6 more)

### Community 50 - "Community 50"
Cohesion: 0.19
Nodes (9): BridgeDataCollector, build_monthly_breakdown(), main(), Convert bridge bars to a DataFrame matching BacktestEngine expectations., Populate engine.live_evidence.price_levels from candle data.          The engine, Convert bridge bars to (LiveCandle list, interpolated tick list)., Group trades by calendar month and compute WR + PF per month., Async WebSocket client that fetches historical candles in chunks. (+1 more)

### Community 51 - "Community 51"
Cohesion: 0.29
Nodes (5): display_announcements(), fetch_announcements(), Fetch announcements from endpoint. Returns dict with announcements and settings., Display announcements panel. Prompts for Enter if require_attention is True., Console

### Community 52 - "Community 52"
Cohesion: 0.29
Nodes (4): Complete the trade record and write to disk., Complete lifecycle record of a single trade., Create a new trade record with pre-trade context., TradeRecord

### Community 53 - "Community 53"
Cohesion: 0.36
Nodes (9): analyze(), _f(), find_reversals(), _load_rows(), main(), _median(), _pip_mult(), EOD reversal analysis over the daemon-processed engine-snapshot store.  Reads re (+1 more)

### Community 54 - "Community 54"
Cohesion: 0.33
Nodes (3): Send an arbitrary JSON message to the bridge., Send a request for historical data through the bridge., Send a message via WebSocket.

### Community 55 - "Community 55"
Cohesion: 0.10
Nodes (14): MarketBufferEngine, Computes dynamic velocity exit trail thresholds from market conditions.      Ada, Args:             config: Optional config dict with:               - realtime_ve, Compute dynamic buffer (exit trail threshold) from market state.          Args:, Real-time velocity trailing with retest detection and dynamic market buffer., Detect if price tested SL area (within window pips) and bounced back up., Calculate aggressiveness based on LIVE conditions, not fixed thresholds., Real-time velocity trailing with dynamic market buffer adaptation.      Key insi (+6 more)

### Community 57 - "Community 57"
Cohesion: 0.50
Nodes (3): ExitEngine, Priority-based exit logic with legacy fallback., Args:             legacy_exit_manager: AdaptiveExitManager instance (fallback)

### Community 58 - "Community 58"
Cohesion: 0.24
Nodes (7): Flatten all positions on the active → wind-down session transition.          F, Close every open position for this symbol/magic. Returns count closed., Send command to execution_bridge.py and return the response., Helper to run a coroutine in both synchronous and asynchronous contexts safely., run_coroutine(), send_execution_command(), _ws_send_cmd()

### Community 59 - "Community 59"
Cohesion: 0.33
Nodes (4): patched_fetch_bars(), patched_load_historical_data(), Return our pre-built candles instead of calling MT5., Return pre-built candles and ticks, bypassing MT5 entirely.

### Community 68 - "Community 68"
Cohesion: 0.33
Nodes (5): on_tick_from_bridge(), Start the dashboard with MT5 bridge client connected to Windows bridge., Bridge tick callback → feed into analyzer and broadcast enriched data., Request M15, H1 and H4 historical candles from the bridge for all symbols., request_historical_candles()

## Knowledge Gaps
- **11 isolated node(s):** `FakeVelocity`, `FakeDisplacement`, `FakeSnapshot`, `FakeTradeState`, `FakeLocationContext` (+6 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `LiveCandle` connect `Community 4` to `Community 0`, `Community 1`, `Community 3`, `Community 5`, `Community 6`, `Community 9`, `Community 13`, `Community 14`, `Community 21`, `Community 22`, `Community 23`, `Community 28`, `Community 35`, `Community 36`, `Community 37`, `Community 38`, `Community 50`, `Community 59`, `Community 69`?**
  _High betweenness centrality (0.186) - this node is a cross-community bridge._
- **Why does `AxonDaemon` connect `Community 36` to `Community 0`, `Community 4`, `Community 6`, `Community 8`, `Community 9`, `Community 12`, `Community 13`, `Community 15`, `Community 16`, `Community 17`, `Community 22`, `Community 23`, `Community 28`, `Community 35`, `Community 37`, `Community 41`, `Community 42`, `Community 49`, `Community 55`, `Community 57`, `Community 58`, `Community 70`?**
  _High betweenness centrality (0.139) - this node is a cross-community bridge._
- **Why does `ReversalModel` connect `Community 28` to `Community 0`, `Community 1`, `Community 3`, `Community 36`, `Community 37`, `Community 4`, `Community 69`, `Community 38`, `Community 9`, `Community 10`, `Community 11`, `Community 13`, `Community 14`, `Community 21`, `Community 23`, `Community 57`, `Community 29`?**
  _High betweenness centrality (0.131) - this node is a cross-community bridge._
- **Are the 22 inferred relationships involving `LiveCandle` (e.g. with `BacktestEngine` and `CandleSetup`) actually correct?**
  _`LiveCandle` has 22 INFERRED edges - model-reasoned connections that need verification._
- **Are the 29 inferred relationships involving `NormalizedVelocity` (e.g. with `AdaptiveExitManager` and `ExitDecision`) actually correct?**
  _`NormalizedVelocity` has 29 INFERRED edges - model-reasoned connections that need verification._
- **Are the 28 inferred relationships involving `DisplacementState` (e.g. with `AdaptiveExitManager` and `ExitDecision`) actually correct?**
  _`DisplacementState` has 28 INFERRED edges - model-reasoned connections that need verification._
- **Are the 18 inferred relationships involving `AxonDaemon` (e.g. with `AdaptiveExitManager` and `DisplacementNormalizer`) actually correct?**
  _`AxonDaemon` has 18 INFERRED edges - model-reasoned connections that need verification._