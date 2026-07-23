# Graph Report - AxonAgent-Agy  (2026-07-23)

## Corpus Check
- 159 files · ~251,240 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1837 nodes · 3298 edges · 119 communities (92 shown, 27 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 166 edges (avg confidence: 0.51)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `3340206c`
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
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 75|Community 75]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 78|Community 78]]
- [[_COMMUNITY_Community 79|Community 79]]
- [[_COMMUNITY_Community 80|Community 80]]
- [[_COMMUNITY_Community 81|Community 81]]
- [[_COMMUNITY_Community 82|Community 82]]
- [[_COMMUNITY_Community 83|Community 83]]
- [[_COMMUNITY_Community 84|Community 84]]
- [[_COMMUNITY_Community 85|Community 85]]
- [[_COMMUNITY_Community 89|Community 89]]
- [[_COMMUNITY_Community 90|Community 90]]
- [[_COMMUNITY_Community 91|Community 91]]
- [[_COMMUNITY_Community 92|Community 92]]
- [[_COMMUNITY_Community 96|Community 96]]
- [[_COMMUNITY_Community 98|Community 98]]
- [[_COMMUNITY_Community 99|Community 99]]
- [[_COMMUNITY_Community 102|Community 102]]
- [[_COMMUNITY_Community 104|Community 104]]
- [[_COMMUNITY_Community 105|Community 105]]
- [[_COMMUNITY_Community 107|Community 107]]
- [[_COMMUNITY_Community 111|Community 111]]
- [[_COMMUNITY_Community 114|Community 114]]
- [[_COMMUNITY_Community 115|Community 115]]
- [[_COMMUNITY_Community 117|Community 117]]
- [[_COMMUNITY_Community 119|Community 119]]
- [[_COMMUNITY_Community 120|Community 120]]
- [[_COMMUNITY_Community 121|Community 121]]
- [[_COMMUNITY_Community 122|Community 122]]
- [[_COMMUNITY_Community 123|Community 123]]

## God Nodes (most connected - your core abstractions)
1. `NormalizedVelocity` - 58 edges
2. `DisplacementState` - 52 edges
3. `AxonDaemon` - 43 edges
4. `EventDetector` - 41 edges
5. `MarketContextBuilder` - 40 edges
6. `EntryStateMachine` - 38 edges
7. `Chat Conversation` - 36 edges
8. `LiveCandle` - 35 edges
9. `✅ All 3 Bugs Fixed` - 34 edges
10. `TestMarketContextBuilder` - 34 edges

## Surprising Connections (you probably didn't know these)
- `TestVelocitySpikeQuietMarket` --uses--> `EntryStateMachine`  [INFERRED]
  tests/test_velocity_spike_quiet_market.py → axonai/realtime/entry_state_machine.py
- `TestDaemonE2E` --uses--> `AxonDaemon`  [INFERRED]
  tests/test_daemon_e2e.py → axonai/realtime/daemon.py
- `TestSmartCooldown` --uses--> `AxonDaemon`  [INFERRED]
  tests/test_smart_cooldown.py → axonai/realtime/daemon.py
- `BridgeDataCollector` --uses--> `BacktestEngine`  [INFERRED]
  run_bridge_backtest.py → axonai/realtime/backtester.py
- `TestMarketContext` --uses--> `NormalizedVelocity`  [INFERRED]
  tests/test_market_context.py → axonai/realtime/velocity_normalizer.py

## Import Cycles
- None detected.

## Communities (119 total, 27 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.09
Nodes (30): Adaptive Exit Engine.  Replaces fixed ATR TP/SL. Uses the Trade Health Monitor a, Dynamic Displacement Buffer Engine for Adaptive Entry Thresholds.  Replaces stat, Price displacement engine — measures movement achieved relative to activity.  Di, Entry State Machine.  Replaces the legacy stateless boolean EntryGate. Impleme, Liquidity Pool Engine.  Upgrades the legacy LevelBehaviorTracker. Instead of jus, FakeCandle, FakeLevel, LocationContext (+22 more)

### Community 1 - "Community 1"
Cohesion: 0.09
Nodes (22): _candle_body_ratio(), CandleSetup, CandleSetupTracker, _is_engulfing(), _is_pin_bar(), _PendingSweep, datetime, LiveCandle (+14 more)

### Community 2 - "Community 2"
Cohesion: 0.10
Nodes (22): MarketContextBuilder, Build MarketContext with quality scores from engine outputs., When some engines agree, score should reflect the ratio., When no engines agree, score should be low., High velocity + impulse displacement + at major level = high confidence., Low velocity + neutral displacement + random location = low confidence., Confidence score should always be 0-100., Multiple active sweeps with low displacement = stop hunting. (+14 more)

### Community 3 - "Community 3"
Cohesion: 0.09
Nodes (24): _apply_env_overrides(), _coerce(), # NOTE: realtime_min_trail_floor_pips is set above (6.0). It used to be, Coerce env-var string to the type of the existing default value., Return the per-symbol confluence-score floor for `symbol`.      Falls back to, Apply AXONAI_* env vars to the config dict in-place., signal_quality_for(), patched_fetch_bars() (+16 more)

### Community 4 - "Community 4"
Cohesion: 0.12
Nodes (9): datetime, Score 0-100: How clear/strong is the reversal signal?          Multi-factor scor, Detect if stops are being hunted/manipulated.          Returns: (stop_hunt_detec, Estimate how many ticks the reversal signal is lagged.          A lagged reversa, Determine overall market context verdict.          Matrix:         - confidence, Track how long displacement has been in current classification., Is the reversal opportunity closing/expiring?          Windows close when:, How many ticks until this signal becomes stale/invalid?          Signals expire (+1 more)

### Community 5 - "Community 5"
Cohesion: 0.14
Nodes (11): Process one tick through all detectors.          Args:             bid: Current, Return current rolling statistics as a dict (for dashboard / debug)., Return level behavior summary., Flush and close the current log file., Append one JSON line to the rolling log file., Remove oldest log files beyond max_log_files., Serialisable snapshot of tick behaviour at a point in time., Processes live ticks through multiple detectors and logs results.      Usage: (+3 more)

### Community 6 - "Community 6"
Cohesion: 0.09
Nodes (14): datetime, LiveCandle, Dedicated thread that polls MT5 for ticks and feeds candle builders.      Call, Expose tick_buffer as a list., Calculate order imbalance across 10s, 60s, and 300s windows., Lazy-load and initialize MT5., Pre-seed active incomplete candles from MT5., Fetch new ticks since last known tick time. (+6 more)

### Community 7 - "Community 7"
Cohesion: 0.25
Nodes (6): DisplacementBufferEngine, DynamicDisplacementThresholds, Compute dynamic displacement thresholds from regime.          Args:, Output: dynamic entry thresholds based on market regime., Computes dynamic displacement thresholds from market regime.      Adapts impulse, Args:             config: Optional config dict with:               - impulse_rat

### Community 8 - "Community 8"
Cohesion: 0.09
Nodes (30): generate_session_summary(), Read reports/dry_run_session.jsonl and print a formatted summary., lookup(), MatrixAction, Velocity-Displacement Matrix Lookup.  Pure function that maps velocity percentil, Actions from velocity-displacement matrix lookup., Matrix lookup: velocity × displacement × location → action.      Args:         v, backtest() (+22 more)

### Community 9 - "Community 9"
Cohesion: 0.17
Nodes (14): ColorFormatter, enable_ansi_escape_sequences(), get_windows_host_ip(), is_windows(), is_wsl(), main(), Launch the Deep Scan Calibrator in continuous mode for a specific symbol., Custom Formatter to add ANSI color coding to log levels in terminal. (+6 more)

### Community 10 - "Community 10"
Cohesion: 0.05
Nodes (38): datetime, Lazily create/return the per-session baseline bucket.          Loaded buckets, Process one tick and return normalized velocity state.          Args:, Reset session baseline (call on session boundary)., Reset the peak velocity tracking (call when entering a trade)., Ticks per second over the last `window_sec` seconds., Compute net and absolute velocity in pips/sec over the velocity window., Ratio of net displacement to total path (0 = chop, 1 = impulse). (+30 more)

### Community 11 - "Community 11"
Cohesion: 0.09
Nodes (19): FakeDisplacement, FakeLocationContext, FakeSnapshot, FakeVelocity, datetime, Trade State Engine: Lifecycle Phase Tracking.  Tracks trade lifecycle phases (, Register a new trade with the state engine., Update trade state every tick.          Args:             price: Current pric (+11 more)

### Community 12 - "Community 12"
Cohesion: 0.16
Nodes (18): get_account_info_via_bridge(), _get_bridge_script(), get_orders_via_bridge(), get_positions_via_bridge(), Any, MT5 Order Bridge for dual-terminal architecture.  Maintains data feed on Exness, Start the order bridge subprocess., Send a command through the bridge subprocess. (+10 more)

### Community 13 - "Community 13"
Cohesion: 0.11
Nodes (15): CandleBuilder, Builds OHLCV candles from raw ticks for a single timeframe., Return the last N closed candle close prices., Return the last N closed candle high prices., Return the last N closed candle low prices., Tests for axonai/realtime core components.  Tests cover: 1. CandleBuilder.feed_t, Support zones below bid, resistance zones above., Feed 2000-word analyst output, verify compression ratio > 0.70. (+7 more)

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
Cohesion: 0.17
Nodes (8): NewsGuard, datetime, Dynamic News Guard — blocks new entries around high-impact economic news.  Pair-, Parse ForexFactory weekly JSON rows into event dicts (keeps prev/forecast/actual, EURUSD / EURUSDm / EURUSD=X → {"EUR", "USD"}., Return (blocked, reason). Blocked if a relevant event is inside the         [pre, Pair- and impact-aware economic-news blackout filter., Fetch the calendar if stale; fall back to disk cache. Returns count.

### Community 18 - "Community 18"
Cohesion: 0.06
Nodes (34): Chat Conversation, Planner Response, Planner Response, Planner Response, Planner Response, Planner Response, Planner Response, Planner Response (+26 more)

### Community 19 - "Community 19"
Cohesion: 0.19
Nodes (20): _direction_from(), EntryDecision, evaluate_peak_entry(), Entry-quality gate for microstructure-peak reversals (currently UNUSED in produc, Map an event direction string to a trade side (fail closed)., Decide whether a PEAK_DETECTION event qualifies for entry.      Returns an Entry, _event(), _gate() (+12 more)

### Community 20 - "Community 20"
Cohesion: 0.10
Nodes (12): BridgeClient, MT5 Bridge Client — runs in WSL, connects to the Windows MT5 Bridge Service and, Receive and relay messages from the bridge., Send an arbitrary JSON message to the bridge., Send a request for historical data through the bridge., Send a message via WebSocket., Check if connected to the bridge., Connects to the Windows MT5 Bridge and relays data to the dashboard.      The br (+4 more)

### Community 21 - "Community 21"
Cohesion: 0.13
Nodes (15): EntryStateMachine, Force the machine back to IDLE., Stateful trade entry execution manager., MarketContext, Should skip entry when reversal_confidence is too low., Should size position lower when reversal is MODERATE vs STRONG., Should ALLOW entry when stop hunt is REVERSING (confirmed)., Signal quality should reflect consensus strength. (+7 more)

### Community 22 - "Community 22"
Cohesion: 0.13
Nodes (16): BacktestEngine, datetime, LiveCandle, Fetch real data from MT5 if connected, otherwise generate synthetic market datas, Load real historical bars from MetaTrader 5 and generate interpolated ticks., Generate realistic multi-phase synthetic market dataset.                  Mark, Historical data simulator and backtesting engine for AxonAI., Pre-populate the H1 and H4 candle deques from the beginning of historical data t (+8 more)

### Community 23 - "Community 23"
Cohesion: 0.06
Nodes (34): 1. How Phase 1 (Event Detection) was modified, 1. Indicators Warm Up Chronologically (`_backfill_history`), 1. The Macro Gate (Candle Setup Tracker), 1. The Root Cause of Immediate Entry/Invalidation on Restart, 2. Extremely Short/Wrong SL & TP for Gold (XAUUSD), 2. How Phase 2 (Live Structural Gates) was modified, 2. The Micro Gate (Entry State Machine), 2. The Tick Engine Starts Cold (`tick_engine.start()`) (+26 more)

### Community 24 - "Community 24"
Cohesion: 0.16
Nodes (8): PeakDetector, PeakSignal, datetime, Peak and climax exhaustion detection engine with advanced tick microstructure me, Detects price exhaustion peaks, volume climaxes, price-per-tick efficiency colla, Update indicators with new tick data and check for peaks.                  Retur, Live tick behavior analysis — velocity, imbalance, microstructure peaks, level i, TestPeakDetector

### Community 25 - "Community 25"
Cohesion: 0.05
Nodes (57): Random, Bar, baseline(), build_bars(), build_venue(), Candidate, _cell_rows(), classify() (+49 more)

### Community 26 - "Community 26"
Cohesion: 0.11
Nodes (10): LevelBehaviorTracker, Return current level behavior state for serialization into MarketEvidence., Direct access for EventDetector queries., Quick check: is price absorbing at this level?, How many times has this level been attacked?, How many consecutive approaches without full pullback?, Remove behaviors for levels no longer active or not seen for a while., Clear all tracked behavior state. (+2 more)

### Community 27 - "Community 27"
Cohesion: 0.15
Nodes (24): broadcast(), data_loop(), ensure_symbol(), get_account_data(), get_candles_data(), get_historical_bars(), get_levels_data(), get_regime_data() (+16 more)

### Community 28 - "Community 28"
Cohesion: 0.16
Nodes (10): MT5TradeExecutor, All open positions on the account (across symbols and magics), used         for, Run the account-wide PortfolioGuard for an order-placing signal., Send a market order with dynamic SL/TP and position sizing to MT5., Handles sending order requests to MetaTrader 5 (either directly or via Execution, Simulate an instant fill at the requested price (paper-trade mode)., Helper to convert OrderSendResult to a dictionary., Cancel an MT5 pending limit/stop order. (+2 more)

### Community 29 - "Community 29"
Cohesion: 0.29
Nodes (3): ExitRecord, ExitStats, Exit Statistics Collector.  Records every exit event with reason, pips, phase, c

### Community 30 - "Community 30"
Cohesion: 0.18
Nodes (14): Velocity spike above 36th percentile in quiet market transitions to ANOMALY., When displacement shows absorption/trap, transition ANOMALY → ARMING., A plain impulse breakaway routes to RETEST_WAIT, not straight to TRIGGERED., entry_require_retest_confirm=False restores the pre-2026-07-23 bypass., Full state machine cycle in quiet market: IDLE → ANOMALY → ARMING → RETEST_WAIT, At exactly 36th percentile, velocity is NOT unusual yet., Above 36th percentile WITH is_unusual=True triggers anomaly., Velocity spike detection in RANGE_CHOP (quiet) market. (+6 more)

### Community 31 - "Community 31"
Cohesion: 0.10
Nodes (11): Test suite for MT5TradeExecutor with mocked MT5 interface., Test that HOLD signals return None and make no calls., Test that dryrun config overrides lot size to 1.00 and returns sl., Paper-trade mode returns a simulated fill and NEVER calls order_send., Successive paper fills get distinct, incrementing synthetic tickets., A spread wider than max_spread_frac of the stop rejects the entry., Test sending a BUY signal order via bridge mode., Test BUY signal order composition and execution. (+3 more)

### Community 32 - "Community 32"
Cohesion: 0.11
Nodes (14): ExitEngine, ExitSignal, FakeDisplacement, FakeLegacyExit, FakeLocationContext, FakeSnapshot, FakeTradeState, FakeVelocity (+6 more)

### Community 33 - "Community 33"
Cohesion: 0.18
Nodes (7): Intraday dynamic liquidity pools created by velocity spikes., Register a new velocity event level or update an existing close level., Check if current price is within the zone of an active level for a retest., Invalidate levels that have been cleanly broken (beyond-extreme)., Clear the registry (call daily/on reset)., VelocityLevel, VelocityLevelRegistry

### Community 34 - "Community 34"
Cohesion: 0.22
Nodes (5): DataFrame, Validate ``value`` is safe to interpolate into a filesystem path.      Tickers, safe_ticker_component(), save_output(), SavePathType

### Community 35 - "Community 35"
Cohesion: 0.09
Nodes (22): EventDetector, datetime, Add trigger candle details to the event details if available., Set pip multiplier based on pair type., Lightweight per-tick checks., Invoke microstructure peak and climax exhaustion detector for both systems., Structural checks on candle close., Detects structural market events from live state.      Detection rules (all pu (+14 more)

### Community 36 - "Community 36"
Cohesion: 0.05
Nodes (40): AxonDaemon, Flatten all positions on the active → wind-down session transition.          F, Close every open position for this symbol/magic. Returns count closed., Called by TickEngine on every new tick., Warm MTF EMAs, regime, and daily levels from historical bars before         the, Called by TickEngine when any timeframe candle closes., Main thread: blocks on event queue, fires graph on valid events., Persistently log every generated signal to reports/signals.jsonl and reports/sig (+32 more)

### Community 38 - "Community 38"
Cohesion: 0.07
Nodes (53): get_config(), initialize_config(), Initialize the configuration with default values., Update the configuration with custom values.      Dict-valued keys (e.g. ``dat, Get the current configuration., set_config(), extract_market_evidence(), MarketEvidence (+45 more)

### Community 39 - "Community 39"
Cohesion: 0.21
Nodes (6): Helper checking if circuit breaker has tripped., Drawdown Circuit Breaker.          Tracks daily profit/loss and halts all execut, Seed daily starting equity on first call of the day., Update realized PnL for the day., Check if circuit breaker has tripped., RiskGuard

### Community 40 - "Community 40"
Cohesion: 0.14
Nodes (11): DisplacementState, Output snapshot of displacement analysis on every tick., DynamicBuffer, MarketBufferEngine, Output: dynamic exit trail threshold for velocity decay., Computes dynamic velocity exit trail thresholds from market conditions.      Ada, Args:             config: Optional config dict with:               - realtime_ve, Compute dynamic buffer (exit trail threshold) from market state.          Args: (+3 more)

### Community 41 - "Community 41"
Cohesion: 0.10
Nodes (15): LiveWorldState, Cold start: build full WorldState from historical bars.         Called once whe, Populate rolling windows from historical MT5 bars., Update spread and session on every tick. O(1) cost., Update indicators when a candle closes., Update ATR, EMA, RSI on H1 candle close., Update H4 EMA on H4 candle close., Recompute regime scores on M15 candle close. (+7 more)

### Community 42 - "Community 42"
Cohesion: 0.15
Nodes (14): _num(), DisplacementState, LiquidityState, MTFState, NormalizedVelocity, Evaluate conditions and transition states., Look for the initial anomaly (Microstructure Peak).          With the candle s, Wait for the anomaly to form a trap or show absorption. (+6 more)

### Community 43 - "Community 43"
Cohesion: 0.11
Nodes (30): _classify_gate(), _g(), _pip(), Trade Analytics Tracker.  Captures the COMPLETE engine decision context (entry s, Records full-context trade history for off-line evaluation., Create a trade record capturing the full entry decision.          entry_price is, Complete the record with exit criteria + engine state at the cut.          exit_, Defensive getattr → coerce to the default's type. (+22 more)

### Community 44 - "Community 44"
Cohesion: 0.22
Nodes (25): _allow_with_room(), _disp(), _liq(), _loc(), _lvl(), _mtf(), Tests for per-pair scaling + the unified confluence gate.  These were written ag, Same inputs with and without the level; the level must raise the score. (+17 more)

### Community 45 - "Community 45"
Cohesion: 0.22
Nodes (7): LiveCandle, An in-memory OHLCV candle built from raw ticks., test_backfill_historical_events(), test_bearish_engulfing_medium_intensity(), test_bullish_engulfing_high_intensity(), test_pin_bar_body_color_enforcement(), test_sweep_detection()

### Community 46 - "Community 46"
Cohesion: 0.21
Nodes (13): handle_client(), http_handler(), main(), _mt5_call(), mt5_init(), Convert MT5 position object to dict., Handle connection from daemon client., Run a blocking MT5 call on the dedicated worker thread and await the result. (+5 more)

### Community 47 - "Community 47"
Cohesion: 0.22
Nodes (7): DynamicVelocityThresholds, Dynamic Velocity Threshold Engine for Adaptive Entry Detection.  Replaces static, Output: dynamic velocity thresholds based on market regime., Computes dynamic velocity thresholds from market regime.      Adapts percentile/, Args:             config: Optional config dict with:               - velocity_pe, Compute dynamic velocity thresholds from regime.          Args:             regi, VelocityThresholdEngine

### Community 48 - "Community 48"
Cohesion: 0.25
Nodes (6): PortfolioGuard, _position_direction(), Portfolio-level pre-trade risk guard.  Account-wide caps enforced before any new, Best-effort BUY/SELL from a bridge position dict or an MT5 position obj.      MT, Account-wide pre-trade checks. Returns (allowed, reason) from `check`., Evaluate all enabled caps. `reason` is '' when allowed.

### Community 49 - "Community 49"
Cohesion: 0.27
Nodes (6): Test MarketContext immutability and structure., MarketContext dataclass must be frozen (immutable)., MarketContext includes all 6 math engine outputs., Summary should be human-readable., Create a basic MarketContext for testing., TestMarketContext

### Community 50 - "Community 50"
Cohesion: 0.21
Nodes (9): LevelBehavior, datetime, Tick-level behavior tracking around price levels.  Monitors how price interacts, Process one tick across all active levels. O(n) where n = levels., Per-level real-time tick behavior tracking state., Initialize a new approach tracking cycle., Enforce max approach duration., Close an active approach and classify outcome. (+1 more)

### Community 51 - "Community 51"
Cohesion: 0.29
Nodes (5): display_announcements(), fetch_announcements(), Fetch announcements from endpoint. Returns dict with announcements and settings., Display announcements panel. Prompts for Enter if require_attention is True., Console

### Community 52 - "Community 52"
Cohesion: 0.15
Nodes (15): get_dashboard(), Get the active global dashboard server instance., High-fidelity historical backtesting engine with candle, peak, reversal, and swe, AxonAI real-time trading daemon.  Always-alive process that monitors MT5 tick, Pure Python event detection engine.  Watches the live state for structural mar, EventPriority, EventType, Event type definitions for the real-time trading engine. (+7 more)

### Community 53 - "Community 53"
Cohesion: 0.18
Nodes (15): main(), analyze(), _default_reversal_pips(), _f(), find_reversals(), _load_rows(), main(), _median() (+7 more)

### Community 54 - "Community 54"
Cohesion: 0.15
Nodes (11): Any, convert_numpy(), Thread-safe queueing of message broadcast across websockets based on symbol rout, Retrieve or create a thread lock specific to a symbol to reduce contention., Register a daemon instance for multicurrency support., Asynchronously send message to sockets, routing by subscription., Aggregate the per-symbol caches into one compact fleet roll-up for         the, Emit fleet_summary (~1 Hz) so the FLEET view sees all symbols at once, (+3 more)

### Community 55 - "Community 55"
Cohesion: 0.09
Nodes (24): Real-time velocity trailing with retest detection and dynamic market buffer., Detect if price tested SL area (within window pips) and bounced back up., Real-time velocity trailing with dynamic market buffer adaptation.      Key insi, Calculate aggressiveness based on LIVE conditions, not fixed thresholds., Momentum-state multiplier in [0.4, 2.5].          High (give the move room) when, Pips to keep behind price, driven by momentum state.          width_mult (from m, Clear trail state when position closes., Reference-ratio scale for pip-distance constants.          effective = original (+16 more)

### Community 57 - "Community 57"
Cohesion: 0.29
Nodes (6): EntryDecision, datetime, Evaluate entry decision using MarketContext quality scores.          Uses qual, The output of the entry state machine., EngineSnapshot, A complete snapshot of all engine states at a given tick.

### Community 58 - "Community 58"
Cohesion: 0.47
Nodes (5): Helper to run a coroutine in both synchronous and asynchronous contexts safely., Send command to execution_bridge.py and return the response., run_coroutine(), send_execution_command(), _ws_send_cmd()

### Community 59 - "Community 59"
Cohesion: 0.08
Nodes (18): get_dst_session_hours(), LiveMarketEvidence, Reset and recalculate all institutional levels daily using local history., Update indicators and handle level invalidation on candle close., Scan self._m15_candles to find local swing highs and swing lows (window of 3) as, Invalidate levels if closed through, too old, etc., Update dynamic indicators (RSI, MACD, trends) from H1 history using config-drive, Detect candle patterns on active timeframe. (+10 more)

### Community 64 - "Community 64"
Cohesion: 0.20
Nodes (4): Regression tests for the freeze-safe execution-safety hardening.  Covers the fix, _sym_info(), TestBridgeTokenAuth, TestExecutionSafety

### Community 72 - "Community 72"
Cohesion: 0.33
Nodes (4): LocationEngine, Computes market location (distance to levels, at_structure flag)., Args:             pip_mult: 0.0001 for majors, 0.01 for JPY pairs             co, Compute market location metrics.          Args:             price: Current bid/a

### Community 75 - "Community 75"
Cohesion: 0.36
Nodes (3): Test session labels from world_state.py session logic., Replicate the session logic from world_state.py lines 167-186., TestSessionDetection

### Community 79 - "Community 79"
Cohesion: 0.08
Nodes (19): _num(), datetime, DisplacementState, LiquidityState, MTFState, NormalizedVelocity, The unified Market-State-Aware Reversal Engine., Update structural support/resistance levels. (+11 more)

### Community 80 - "Community 80"
Cohesion: 0.29
Nodes (7): ✅ All 3 Bugs Fixed, Planner Response, Planner Response, Planner Response, User Input, User Input, User Input

### Community 81 - "Community 81"
Cohesion: 0.19
Nodes (9): BridgeDataCollector, build_monthly_breakdown(), main(), Convert bridge bars to a DataFrame matching BacktestEngine expectations., Populate engine.live_evidence.price_levels from candle data.          The engine, Convert bridge bars to (LiveCandle list, interpolated tick list)., Group trades by calendar month and compute WR + PF per month., Async WebSocket client that fetches historical candles in chunks. (+1 more)

### Community 82 - "Community 82"
Cohesion: 0.29
Nodes (6): 1. Missing Backend Broadcasts (Data not being sent), 2. Missing Frontend UI Components (Dashboard UI), 3. What is NOT Necessary (Avoid Clutter), AxonAI Dashboard Gap Analysis, Conclusion & Next Steps, Overview

### Community 83 - "Community 83"
Cohesion: 0.46
Nodes (7): alignbucket(), confbucket(), get(), isW(), num(), summarize(), zbucket()

### Community 84 - "Community 84"
Cohesion: 0.20
Nodes (6): Notification and alert dispatch module for AxonAI., Dispatch an alert message to configured destinations (Telegram, Webhook)., send_alert(), Risk management and drawdown circuit breaker for AxonAI.  Monitors daily profit/, Trade execution module for MetaTrader 5.  Performs live order routing, positio, Unit and integration tests for MT5TradeExecutor.

### Community 85 - "Community 85"
Cohesion: 0.18
Nodes (8): DisplacementEngine, datetime, NormalizedVelocity, Process one tick and return displacement state.          Args:             price, Summarize recent displacement direction.          Returns: "bullish", "bearish",, Detect when displacement classification changes rapidly., Multi-factor displacement classification.          Decision matrix:           Hi, Computes displacement metrics from raw tick data + velocity context.      Design

### Community 89 - "Community 89"
Cohesion: 0.29
Nodes (4): PriceLevel, Reset PWH/PWL weekly using local history., DummyDashboard, Mock dashboard to force BridgeClient to broadcast candles/levels to us.

### Community 90 - "Community 90"
Cohesion: 0.11
Nodes (23): AdaptiveExitManager, ExitDecision, Evaluate if we should hold, adjust SL/TP, or force close., Output of the exit evaluation., Manages active trades, adjusting targets or cutting losses dynamically., Register the trade to track., Clear active tracking., LiquidityState (+15 more)

### Community 91 - "Community 91"
Cohesion: 0.18
Nodes (10): LevelState, LiquidityEngine, LiquidityEvent, datetime, Process one tick against all active levels., Determine if an active breach is a sweep or a structural break., Represents a specific interaction with a price level., Live state of a single price level. (+2 more)

### Community 92 - "Community 92"
Cohesion: 0.33
Nodes (5): on_tick_from_bridge(), Start the dashboard with MT5 bridge client connected to Windows bridge., Bridge tick callback → feed into analyzer and broadcast enriched data., Request M15, H1 and H4 historical candles from the bridge for all symbols., request_historical_candles()

### Community 96 - "Community 96"
Cohesion: 0.24
Nodes (6): MTFContext, LiveCandle, Calculate the current multi-timeframe alignment., Calculate a -1.0 to 1.0 bias score for a specific timeframe., Computes multi-timeframe alignment scores using EMAs and structure., Update state when a candle closes on any timeframe.

### Community 107 - "Community 107"
Cohesion: 0.11
Nodes (14): DashboardServer, FastAPI WebSocket server for real-time visual signaling dashboard.  Integrates, Save event history, latest decision, and levels state to disk., Load session state from disk on startup., Launch the API and web server in a daemon thread., Background thread: periodically fetches the economic calendar from NewsGuard., Bind endpoints to FastAPI app., Target for Uvicorn runner inside the thread. (+6 more)

## Knowledge Gaps
- **88 isolated node(s):** `User Input`, `Planner Response`, `Planner Response`, `Planner Response`, `Planner Response` (+83 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **27 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `EntryStateMachine` connect `Community 21` to `Community 0`, `Community 70`, `Community 42`, `Community 79`, `Community 57`, `Community 30`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Why does `AxonDaemon` connect `Community 36` to `Community 8`, `Community 16`, `Community 43`, `Community 52`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Why does `LiveCandle` connect `Community 45` to `Community 0`, `Community 35`, `Community 37`, `Community 14`, `Community 81`, `Community 52`, `Community 89`?**
  _High betweenness centrality (0.058) - this node is a cross-community bridge._
- **Are the 19 inferred relationships involving `NormalizedVelocity` (e.g. with `AdaptiveExitManager` and `ExitDecision`) actually correct?**
  _`NormalizedVelocity` has 19 INFERRED edges - model-reasoned connections that need verification._
- **Are the 18 inferred relationships involving `DisplacementState` (e.g. with `AdaptiveExitManager` and `ExitDecision`) actually correct?**
  _`DisplacementState` has 18 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `AxonDaemon` (e.g. with `TradeAnalytics` and `TestDaemonE2E`) actually correct?**
  _`AxonDaemon` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `EventDetector` (e.g. with `EventPriority` and `EventType`) actually correct?**
  _`EventDetector` has 12 INFERRED edges - model-reasoned connections that need verification._