# Graph Report - AxonAgent-Agy  (2026-07-06)

## Corpus Check
- 85 files · ~84,824 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1290 nodes · 2789 edges · 59 communities (53 shown, 6 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 282 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `9bafc02c`
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
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 65|Community 65]]

## God Nodes (most connected - your core abstractions)
1. `NormalizedVelocity` - 83 edges
2. `DisplacementState` - 74 edges
3. `LiveCandle` - 69 edges
4. `AxonDaemon` - 54 edges
5. `RegimeState` - 53 edges
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

## Communities (59 total, 6 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.07
Nodes (42): AdaptiveExitManager, ExitDecision, Adaptive Exit Engine.  Replaces fixed ATR TP/SL. Uses the Trade Health Monitor a, Evaluate if we should hold, adjust SL/TP, or force close., Output of the exit evaluation., Manages active trades, adjusting targets or cutting losses dynamically., Clear active tracking., DisplacementState (+34 more)

### Community 1 - "Community 1"
Cohesion: 0.08
Nodes (25): EntryDecision, EntryStateMachine, datetime, Evaluate conditions and transition states., Evaluate entry decision using MarketContext quality scores.          Uses qual, Look for the initial anomaly (Microstructure Peak)., Wait for the anomaly to form a trap or show absorption., Wait for the price to break away from the trap in our direction. (+17 more)

### Community 2 - "Community 2"
Cohesion: 0.10
Nodes (22): MarketContextBuilder, Build MarketContext with quality scores from engine outputs., When some engines agree, score should reflect the ratio., When no engines agree, score should be low., High velocity + impulse displacement + at major level = high confidence., Low velocity + neutral displacement + random location = low confidence., Confidence score should always be 0-100., Multiple active sweeps with low displacement = stop hunting. (+14 more)

### Community 3 - "Community 3"
Cohesion: 0.07
Nodes (39): DisplacementBufferEngine, DynamicDisplacementThresholds, Dynamic Displacement Buffer Engine for Adaptive Entry Thresholds.  Replaces stat, Compute dynamic displacement thresholds from regime.          Args:, Output: dynamic entry thresholds based on market regime., Computes dynamic displacement thresholds from market regime.      Adapts impulse, Args:             config: Optional config dict with:               - impulse_rat, DisplacementEngine (+31 more)

### Community 4 - "Community 4"
Cohesion: 0.10
Nodes (21): EventDetector, datetime, Set pip multiplier based on pair type., Lightweight per-tick checks., Invoke microstructure peak and climax exhaustion detector for both systems., Structural checks on candle close., Detects structural market events from live state.      Detection rules (all pu, Check for breach of active institutional levels on tick.          Uses LevelBe (+13 more)

### Community 5 - "Community 5"
Cohesion: 0.08
Nodes (43): _ensure_symbol_visible(), _fetch_bars(), get_broker_tz_offset(), get_mt5_atr(), get_mt5_indicators(), get_mt5_live_price(), get_mt5_stock_data(), get_mt5_ticks() (+35 more)

### Community 6 - "Community 6"
Cohesion: 0.14
Nodes (8): Dedicated thread that polls MT5 for ticks and feeds candle builders.      Call, Expose tick_buffer as a list., Calculate order imbalance across 10s, 60s, and 300s windows., Fetch new ticks since last known tick time., Update bid/ask, feed candle builders, invoke callbacks., Signal the thread to stop., Current spread in raw price units., TickEngine

### Community 7 - "Community 7"
Cohesion: 0.09
Nodes (24): FastAPI WebSocket server for real-time visual signaling dashboard.  Integrates, EventPriority, EventType, Event type definitions for the real-time trading engine., axonai.realtime – Real-time trading engine components., Real-time tick ingestion engine.  Continuously polls MT5 for raw tick data and, Queue, End-to-End mock integration test for AxonDaemon. (+16 more)

### Community 8 - "Community 8"
Cohesion: 0.09
Nodes (30): generate_session_summary(), Read reports/dry_run_session.jsonl and print a formatted summary., lookup(), MatrixAction, Velocity-Displacement Matrix Lookup.  Pure function that maps velocity percentil, Actions from velocity-displacement matrix lookup., Matrix lookup: velocity × displacement × location → action.      Args:         v, backtest() (+22 more)

### Community 9 - "Community 9"
Cohesion: 0.07
Nodes (27): extract_market_evidence(), MarketEvidence, Extract structured facts from raw MT5 data in pure Python. Fail safe if MT5 is u, get_dst_session_hours(), LiveMarketEvidence, PriceLevel, datetime, Incrementally updated live market state.  Seeds from historical MT5 bars once (+19 more)

### Community 10 - "Community 10"
Cohesion: 0.06
Nodes (25): datetime, Lazily create/return the per-session baseline bucket.          Loaded buckets re, Process one tick and return normalized velocity state.          Args:, Reset session baseline (call on session boundary)., Reset the peak velocity tracking (call when entering a trade)., Ticks per second over the last `window_sec` seconds., Compute net and absolute velocity in pips/sec over the velocity window., Ratio of net displacement to total path (0 = chop, 1 = impulse). (+17 more)

### Community 11 - "Community 11"
Cohesion: 0.09
Nodes (19): FakeDisplacement, FakeLocationContext, FakeSnapshot, FakeVelocity, datetime, Trade State Engine: Lifecycle Phase Tracking.  Tracks trade lifecycle phases (, Register a new trade with the state engine., Update trade state every tick.          Args:             price: Current pric (+11 more)

### Community 12 - "Community 12"
Cohesion: 0.07
Nodes (26): get_dashboard(), Get the active global dashboard server instance., AxonDaemon, datetime, Called by TickEngine when any timeframe candle closes., Main thread: blocks on event queue, fires graph on valid events., Persistently log every generated signal to reports/signals.jsonl and reports/sig, Smart dynamic cooldown based on trade outcome and direction. (+18 more)

### Community 13 - "Community 13"
Cohesion: 0.06
Nodes (29): BacktestEngine, Any, datetime, High-fidelity historical backtesting engine with candle, peak, reversal, and swe, Generate realistic multi-phase synthetic market dataset.                  Market, Pre-populate the H1 and H4 candle deques from the beginning of historical data t, Historical data simulator and backtesting engine for AxonAI., Execute the backtest simulation sequentially, detecting events and managing trad (+21 more)

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
Cohesion: 0.07
Nodes (23): convert_numpy(), DashboardServer, Any, Recursively convert numpy types to native Python types for JSON serialization., Manages the FastAPI lifecycle and WebSocket broadcasts., Send all cached state history to a newly connected client., Thread-safe queueing of message broadcast across all websockets., Asynchronously send message to all sockets. (+15 more)

### Community 18 - "Community 18"
Cohesion: 0.15
Nodes (24): broadcast(), data_loop(), ensure_symbol(), get_account_data(), get_candles_data(), get_historical_bars(), get_levels_data(), get_regime_data() (+16 more)

### Community 19 - "Community 19"
Cohesion: 0.19
Nodes (20): _direction_from(), EntryDecision, evaluate_peak_entry(), Shared entry-quality gate for microstructure-peak reversals.  Single source of t, Map an event direction string to a trade side (fail closed)., Decide whether a PEAK_DETECTION event qualifies for entry.      Returns an Entry, _event(), _gate() (+12 more)

### Community 20 - "Community 20"
Cohesion: 0.07
Nodes (25): Set the global fallback path for the feed terminal (e.g. Exness).      CRITICA, set_feed_terminal_path(), Helper to initialize and start the global dashboard server., start_dashboard(), BridgeClient, MT5 Bridge Client — runs in WSL, connects to the Windows MT5 Bridge Service and, Receive and relay messages from the bridge., Send an arbitrary JSON message to the bridge. (+17 more)

### Community 21 - "Community 21"
Cohesion: 0.17
Nodes (14): When displacement shows absorption/trap, transition ANOMALY → ARMING., When in ARMING and price breaks away with impulse, transition to TRIGGERED., Full state machine cycle in quiet market: IDLE → ANOMALY → ARMING → TRIGGERED., At exactly 36th percentile, velocity is NOT unusual yet., Above 36th percentile WITH is_unusual=True triggers anomaly., Velocity spike detection in RANGE_CHOP (quiet) market., Create a mock NormalizedVelocity., Create a mock DisplacementState. (+6 more)

### Community 22 - "Community 22"
Cohesion: 0.09
Nodes (14): Warm MTF EMAs, regime, and daily levels from historical bars before         the, Pure Python event detection engine.  Watches the live state for structural mar, LiveCandle, An in-memory OHLCV candle built from raw ticks., LiveWorldState, Update indicators when a candle closes., Update ATR, EMA, RSI on H1 candle close., Update H4 EMA on H4 candle close. (+6 more)

### Community 23 - "Community 23"
Cohesion: 0.12
Nodes (9): datetime, Calculate what % of engines agree on reversal.          Returns: (agreement_scor, Score 0-100: How clear/strong is the reversal signal?          Multi-factor scor, Estimate how many ticks the reversal signal is lagged.          A lagged reversa, Determine overall market context verdict.          Matrix:         - confidence, Track how long displacement has been in current classification., Is the reversal opportunity closing/expiring?          Windows close when:, How many ticks until this signal becomes stale/invalid?          Signals expire (+1 more)

### Community 24 - "Community 24"
Cohesion: 0.07
Nodes (24): PeakDetector, PeakSignal, datetime, Peak and climax exhaustion detection engine with advanced tick microstructure me, Detects price exhaustion peaks, volume climaxes, price-per-tick efficiency colla, Update indicators with new tick data and check for peaks.                  Retur, Live tick behavior analysis — velocity, imbalance, microstructure peaks, level i, Process one tick through all detectors.          Args:             bid: Current (+16 more)

### Community 25 - "Community 25"
Cohesion: 0.14
Nodes (10): CandleBuilder, datetime, Builds OHLCV candles from raw ticks for a single timeframe., Compute the period start time for a given timestamp., Process a tick. Returns a closed candle if the period boundary was crossed., Return the last N closed candle close prices., Return the last N closed candle high prices., Return the last N closed candle low prices. (+2 more)

### Community 26 - "Community 26"
Cohesion: 0.12
Nodes (8): LevelBehaviorTracker, Tick-level behavior tracking around price levels.  Monitors how price interacts, Quick check: is price absorbing at this level?, How many times has this level been attacked?, How many consecutive approaches without full pullback?, Remove behaviors for levels no longer active or not seen for a while., Clear all tracked behavior state., Tracks tick-level interactions with all active PriceLevels.      Call ``update(m

### Community 27 - "Community 27"
Cohesion: 0.10
Nodes (23): get_config(), initialize_config(), Initialize the configuration with default values., Update the configuration with custom values.      Dict-valued keys (e.g. ``dat, Get the current configuration., set_config(), _apply_env_overrides(), _coerce() (+15 more)

### Community 28 - "Community 28"
Cohesion: 0.13
Nodes (21): get_account_info_via_bridge(), _get_bridge_script(), get_positions_via_bridge(), Any, MT5 Order Bridge for dual-terminal architecture.  Maintains data feed on Exness, Start the order bridge subprocess., Send a command through the bridge subprocess., Send an order through the bridge subprocess.      Args:         mt5_trade_termin (+13 more)

### Community 29 - "Community 29"
Cohesion: 0.19
Nodes (9): BridgeDataCollector, build_monthly_breakdown(), main(), Convert bridge bars to a DataFrame matching BacktestEngine expectations., Populate engine.live_evidence.price_levels from candle data.          The engine, Convert bridge bars to (LiveCandle list, interpolated tick list)., Group trades by calendar month and compute WR + PF per month., Async WebSocket client that fetches historical candles in chunks. (+1 more)

### Community 30 - "Community 30"
Cohesion: 0.12
Nodes (12): MarketBufferEngine, Computes dynamic velocity exit trail thresholds from market conditions.      Ada, Args:             config: Optional config dict with:               - realtime_ve, Detect if price tested SL area (within window pips) and bounced back up., Calculate aggressiveness based on LIVE conditions, not fixed thresholds., Momentum-state multiplier in [0.4, 2.5].          High (give the move room) when, Pips to keep behind price, driven by momentum state.          width_mult (from m, Clear trail state when position closes. (+4 more)

### Community 31 - "Community 31"
Cohesion: 0.12
Nodes (9): Test suite for MT5TradeExecutor with mocked MT5 interface., Test that HOLD signals return None and make no calls., Test that dryrun config overrides lot size to 1.00 and returns sl., Paper-trade mode returns a simulated fill and NEVER calls order_send., Successive paper fills get distinct, incrementing synthetic tickets., A spread wider than max_spread_frac of the stop rejects the entry., Test BUY signal order composition and execution., Test SELL signal order composition and execution. (+1 more)

### Community 32 - "Community 32"
Cohesion: 0.14
Nodes (11): ExitSignal, FakeDisplacement, FakeLegacyExit, FakeLocationContext, FakeSnapshot, FakeTradeState, FakeVelocity, Exit Engine: Priority-Based Trade Exit Logic.  Evaluates exit conditions using (+3 more)

### Community 33 - "Community 33"
Cohesion: 0.15
Nodes (11): LevelBehavior, datetime, Process one tick across all active levels. O(n) where n = levels., Return current level behavior state for serialization into MarketEvidence., Direct access for EventDetector queries., Per-level real-time tick behavior tracking state., Initialize a new approach tracking cycle., Enforce max approach duration. (+3 more)

### Community 34 - "Community 34"
Cohesion: 0.22
Nodes (5): DataFrame, Validate ``value`` is safe to interpolate into a filesystem path.      Tickers, safe_ticker_component(), save_output(), SavePathType

### Community 35 - "Community 35"
Cohesion: 0.43
Nodes (6): Map a peak event to a trade side. Returns 'BUY', 'SELL', or None.          Sin, _evt(), Unit tests for AxonDaemon._entry_direction — the single source of truth for mapp, test_bearish_maps_to_sell(), test_bullish_maps_to_buy(), test_indeterminate_fails_closed()

### Community 37 - "Community 37"
Cohesion: 0.18
Nodes (10): LevelState, LiquidityEngine, LiquidityEvent, datetime, Process one tick against all active levels., Determine if an active breach is a sweep or a structural break., Represents a specific interaction with a price level., Live state of a single price level. (+2 more)

### Community 38 - "Community 38"
Cohesion: 0.36
Nodes (3): Test session labels from world_state.py session logic., Replicate the session logic from world_state.py lines 167-186., TestSessionDetection

### Community 39 - "Community 39"
Cohesion: 0.21
Nodes (6): Helper checking if circuit breaker has tripped., Drawdown Circuit Breaker.          Tracks daily profit/loss and halts all execut, Seed daily starting equity on first call of the day., Update realized PnL for the day., Check if circuit breaker has tripped., RiskGuard

### Community 40 - "Community 40"
Cohesion: 0.50
Nodes (3): DynamicBuffer, Output: dynamic exit trail threshold for velocity decay., Compute dynamic buffer (exit trail threshold) from market state.          Args:

### Community 41 - "Community 41"
Cohesion: 0.23
Nodes (8): MT5TradeExecutor, Any, Handles sending order requests to MetaTrader 5 (either directly or via Execution, Simulate an instant fill at the requested price (paper-trade mode)., Helper to convert OrderSendResult to a dictionary., Get MT5 instance: prefer trade terminal (dual-terminal), fall back to main MT5., Convert a 5-tier signal into an MT5 order action.          Signals: Buy, Overw, Send a market order with dynamic SL/TP and position sizing to MT5.

### Community 42 - "Community 42"
Cohesion: 0.40
Nodes (3): datetime, Process one tick and return displacement state.          Args:             price, Multi-factor displacement classification.          Decision matrix:           Hi

### Community 44 - "Community 44"
Cohesion: 0.25
Nodes (3): ExitRecord, ExitStats, Exit Statistics Collector.  Records every exit event with reason, pips, phase, c

### Community 45 - "Community 45"
Cohesion: 0.25
Nodes (4): MarketContext dataclass must be frozen (immutable)., MarketContext includes all 6 math engine outputs., Summary should be human-readable., Create a basic MarketContext for testing.

### Community 46 - "Community 46"
Cohesion: 0.27
Nodes (8): handle_client(), main(), mt5_init(), Initialize MT5 connection to the target execution terminal., Convert MT5 position object to dict., Handle connection from daemon client., run_http_server(), serialize_position()

### Community 47 - "Community 47"
Cohesion: 0.28
Nodes (5): MTFContext, Calculate a -1.0 to 1.0 bias score for a specific timeframe., Computes multi-timeframe alignment scores using EMAs and structure., Update state when a candle closes on any timeframe., Calculate the current multi-timeframe alignment.

### Community 48 - "Community 48"
Cohesion: 0.18
Nodes (8): get_mt5_trade(), Get the MT5 instance for trade execution., Notification and alert dispatch module for AxonAI., Dispatch an alert message to configured destinations (Telegram, Webhook)., send_alert(), Risk management and drawdown circuit breaker for AxonAI.  Monitors daily profit/, Trade execution module for MetaTrader 5.  Performs live order routing, positio, Unit and integration tests for MT5TradeExecutor.

### Community 51 - "Community 51"
Cohesion: 0.29
Nodes (5): display_announcements(), fetch_announcements(), Fetch announcements from endpoint. Returns dict with announcements and settings., Display announcements panel. Prompts for Enter if require_attention is True., Console

## Knowledge Gaps
- **11 isolated node(s):** `FakeVelocity`, `FakeDisplacement`, `FakeSnapshot`, `FakeTradeState`, `FakeLocationContext` (+6 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `LiveCandle` connect `Community 22` to `Community 0`, `Community 1`, `Community 3`, `Community 4`, `Community 5`, `Community 6`, `Community 7`, `Community 9`, `Community 43`, `Community 12`, `Community 13`, `Community 14`, `Community 47`, `Community 25`, `Community 28`, `Community 29`?**
  _High betweenness centrality (0.214) - this node is a cross-community bridge._
- **Why does `AxonDaemon` connect `Community 12` to `Community 0`, `Community 35`, `Community 4`, `Community 6`, `Community 7`, `Community 8`, `Community 9`, `Community 41`, `Community 13`, `Community 15`, `Community 16`, `Community 17`, `Community 20`, `Community 22`, `Community 28`, `Community 30`?**
  _High betweenness centrality (0.199) - this node is a cross-community bridge._
- **Why does `ReversalModel` connect `Community 0` to `Community 1`, `Community 3`, `Community 36`, `Community 37`, `Community 9`, `Community 10`, `Community 11`, `Community 12`, `Community 13`, `Community 44`, `Community 47`, `Community 14`, `Community 22`, `Community 28`?**
  _High betweenness centrality (0.109) - this node is a cross-community bridge._
- **Are the 29 inferred relationships involving `NormalizedVelocity` (e.g. with `AdaptiveExitManager` and `ExitDecision`) actually correct?**
  _`NormalizedVelocity` has 29 INFERRED edges - model-reasoned connections that need verification._
- **Are the 28 inferred relationships involving `DisplacementState` (e.g. with `AdaptiveExitManager` and `ExitDecision`) actually correct?**
  _`DisplacementState` has 28 INFERRED edges - model-reasoned connections that need verification._
- **Are the 16 inferred relationships involving `LiveCandle` (e.g. with `BacktestEngine` and `AxonDaemon`) actually correct?**
  _`LiveCandle` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 17 inferred relationships involving `AxonDaemon` (e.g. with `AdaptiveExitManager` and `DisplacementNormalizer`) actually correct?**
  _`AxonDaemon` has 17 INFERRED edges - model-reasoned connections that need verification._