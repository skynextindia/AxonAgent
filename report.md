# AxonAI Core Project Reanalysis Report

A comprehensive technical audit of the AxonAI event-driven multi-agent trading system, comparing current state, fundamental execution, and design milestones.

---

## 1. System Architecture Map

The system is constructed as a structured, low-latency event-driven architecture dividing responsibilities cleanly between high-frequency ingestion, mathematical structure extraction, discrete event detection, and multi-agent execution.

### Ingestion & Candlestick Modeling (Layer 1)
- **Tick Ingest Engine**: [tick_engine.py](file:///d:/work/TradingAgents/axonai/realtime/tick_engine.py)
  - Continuously polls the MetaTrader 5 terminal in a dedicated high-priority daemon thread.
  - Builds, maintains, and pre-seeds multi-timeframe OHLCV candles (`M1`, `M5`, `M15`, `H1`, and `H4`).
- **Candle Data Structures**: [event_types.py](file:///d:/work/TradingAgents/axonai/realtime/event_types.py)
  - Defines `LiveCandle` containing mathematical attributes like ranges, body sizes, shadow depths, and directionality.

### Structural Evidence Extraction (Layer 2)
- **Static Extractor**: [evidence_extractor.py](file:///d:/work/TradingAgents/axonai/dataflows/evidence_extractor.py)
  - Performs cold-start extraction of swing highs, swing lows, key support/resistance levels, and historical session boundaries.
- **Incremental State Manager**: [live_state.py](file:///d:/work/TradingAgents/axonai/realtime/live_state.py)
  - Keeps a dynamic `LiveWorldState` and `LiveMarketEvidence` updated incrementally from incoming ticks and closed candles without expensive full rebuilds.
  - Dynamically updates session ranges (Asian, London, New York) in real-time.

### Event Detection (Layer 3)
- **Pure Math Event Detector**: [event_detector.py](file:///d:/work/TradingAgents/axonai/realtime/event_detector.py)
  - Evaluates ticks and closed candles against pure mathematical bounds (zero LLM token consumption).
  - Emits `MarketEvent` signals when detecting:
    - `STRUCTURE_BREAK` (BOS on M15, H1, H4).
    - `SWEEP_DETECTED` (Liquidity sweeps on M15, H1, H4).
    - `CANDLE_PATTERN` (Pin Bars and Engulfing Confluences on M15, H1, H4).
    - `LEVEL_BREACH`, `VOLATILITY_SPIKE`, and `REGIME_SHIFT`.

### Agent Decision Graph (Layer 4)
- **Graph Executor**: [graph_executor.py](file:///d:/work/TradingAgents/axonai/realtime/graph_executor.py)
  - Coordinates asynchronous LLM execution through a discrete LangGraph compiler.
  - Fires the multi-agent graph dynamically on actionable `HIGH` priority events when the regime conviction is above gating bounds.

### Telemetry Cockpit (Visual UI Layer)
- **API Server & Broadcast Handler**: [api_server.py](file:///d:/work/TradingAgents/axonai/realtime/api_server.py)
  - FastAPI WebSocket server broadcasting ticks, regimes, levels, candle histories, and mind traces to connected dashboard clients.
- **Visual Dashboard**: [index.html](file:///d:/work/TradingAgents/cli/static/index.html)
  - A premium, thinned-down, responsive chart built on Lightweight Charts, drawing real-time ticks, multi-timeframe events, and high-frequency session ranges.

---

## 2. Core Correctness & Upgrades Verification

### Multi-Timeframe Integration & Isolation
- **H4 Timeframe Support**: Added `"H4": 14400` to the tick engine period registry. Historical seeding and active candle creation are fully integrated.
- **Crosshair Hover Isolation**: Resolved marker overlapping on charts. Hovering over a marker on `M15`, `H1`, or `H4` resolves timestamps to the specific timeframe boundaries (`900`, `3600`, and `14400` seconds respectively) to isolate detail wicks.

### Dynamic Session Upgrades
- **New York Session Boundaries**: Defined New York Session boundaries as UTC 13:00 to 21:00.
- **Historical Seeding**: M15 historical bars are scanned to compute exact high/low levels for all sessions (Asian, London, New York).
- **Real-Time Tick Enrichment**: Ticks received during active session windows dynamically raise session highs and lows immediately without waiting for candle closures.

### Pass Evidence (Test suite & UI check)
- **Visual Dashboard**: Screenshot confirms crisp, realistic EURUSD wicks, isolated timeframe markers, thinned crosshairs/grids, and the newly integrated **NY HIGH** and **NY LOW** price lines matching historical Extremes.
- **Pytest Suite**: Complete pass on all 248 collected items (247 passed, 1 skipped for DeepSeek API Key).

---

## 3. Structural Gaps & Weaknesses

### 1. Hardcoded Threshold Gating
- **Location**: [live_state.py:343](file:///d:/work/TradingAgents/axonai/realtime/live_state.py#L343)
- **Weakness**: Gating for `should_run_graph` is hardcoded to a strict conviction boundary of `0.60`. During low-volatility regimes or trending markets with compressed spreads, this hard limit can suppress high-quality breakouts.

### 2. High-Frequency Polling Overhead
- **Location**: [tick_engine.py:290](file:///d:/work/TradingAgents/axonai/realtime/tick_engine.py#L290)
- **Weakness**: The daemon thread continuously sleep-polls MT5 via `time.sleep(self.poll_interval_ms / 1000.0)` every 100ms. In high-frequency environments, this creates excessive polling load on local system resources.

### 3. Lack of Swing Level Aging
- **Location**: [live_state.py:641-649](file:///d:/work/TradingAgents/axonai/realtime/live_state.py#L641)
- **Weakness**: Old swing highs and lows are discarded strictly on a FIFO deque basis (max 5 levels). Old structural levels that have remained un-swept for days are dropped, ignoring historical support/resistance significance.

---

## 4. Top 5 Actionable Recommendations

### 1. Implement Dynamic Volatility-Based Gating (High Impact)
- **Rationale**: Instead of a hard `0.60` belief score threshold, adjust the graph execution gate dynamically using the 14-period H1 ATR. Lower volatility should require tighter spreads, while high-conviction breakout regimes should relax belief thresholds to capture impulsive moves.

### 2. Move Ingestion to Event-Driven Pull/Push
- **Rationale**: Transition from strict 100ms polling sleep loops in `tick_engine.py` to MetaTrader 5's asynchronous push handlers (such as custom IPC push notifications) to decrease CPU overhead and tick latency.

### 3. Implement Multi-Pair Cross-Correlation
- **Rationale**: Expand `LiveWorldState._update_currency_strength` to scan minor USD/EUR crosses (GBPUSD, USDCHF, AUDUSD) rather than relying solely on single-pair momentum approximations. This will make currency strength valuations statistically stable and robust.

### 4. Implement Historical Support/Resistance Level Decay
- **Rationale**: Maintain swing levels in a time-weighted priority queue rather than a flat FIFO deque. Old swing levels should decay in strength slowly over time unless reinforced by multiple price interactions.

### 5. Add Live SL/TP Visual Adjusters in UI
- **Rationale**: Allow the trade cockpit to dynamically render dynamic Stop Loss and Take Profit levels on the chart directly, enabling manual click-and-drag adjusters to simplify trade management.
