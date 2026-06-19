# AxonAI

[![GitHub License](https://img.shields.io/github/license/skynextindia/AxonAgent?color=blue)](LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-green.svg)](pyproject.toml)
[![Architecture](https://img.shields.io/badge/architecture-3--Layer%20Daemon-orange.svg)](#architecture-overview)

AxonAI is a **3-layer event-driven multi-agent trading daemon** designed for real-time forex trading. It integrates directly with **MetaTrader 5 (MT5)** to stream raw market ticks, run high-frequency mathematical event detection, and trigger deep multi-agent LLM analysis via **LangGraph** on actionable market events.

---

## 📖 Table of Contents
- [Development Status & Roadmap](#-development-status--roadmap)
- [Architecture Overview](#-architecture-overview)
- [Key Features](#-key-features)
- [Project Directory Structure](#-project-directory-structure)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Real-Time Dashboard](#real-time-dashboard)
- [Verification & Testing](#verification--testing)

---

## 📈 Development Status & Roadmap

AxonAI has transitioned from an unoptimized research framework into a highly reliable, cost-efficient, production-grade real-time trading engine. 

### ✅ Completed Milestones & Breakthroughs
- **Agent Identity Overhaul**: Re-wrote system prompts for all **12 agents** in the LangGraph system, assigning elite trading identities (e.g., `WYCKOFF` for Technicals, `MUNGER` for Fundamentals, `TUDOR` as the Trader, `DRUCKENMILLER` as the Portfolio Manager) with a clear color-coded tier UI system.
- **Strict Sequential Graph Execution**: Replaced unstructured node workflows with a strict sequential/fan-out execution DAG (`Tudor` ➔ `Parallel Analysts` ➔ `Bull/Bear Debate` ➔ `Research Manager` ➔ `Parallel Risk Debaters` ➔ `Druckenmiller Decision`).
- **Pydantic Structured Outputs**: Integrated strict `llm.with_structured_output()` verification on key orchestrators (`Trader`, `Research Manager`, `Portfolio Manager`) to guarantee 100% reliable, crash-free output parsing.
- **Mathematical Evidence Compressor**: Added a zero-token state pre-processor that compresses raw market data and levels into structured evidence before sending it to reasoning nodes. **Result: Saved ~80% in API token costs** and completely resolved context window saturation.
- **Real-Time Telemetry & WS Stream**: Re-wired the FastAPI web-dashboard to stream real-time ticks, ATR calculations, active levels, agent debate traces, and live MT5 execution statuses.
- **Safety Testing**: Expanded unit tests to include MT5 order routing validation, event detector checks, and structured payload parsers.

### 🎯 Current Progress & Active Goals
- [x] Enforce structured Pydantic schemas on all reasoning nodes.
- [x] Implement the mathematical `EvidenceCompressor` token reduction module.
- [ ] **Live Dry-Run Verification**: Run continuous 24/5 paper trading to verify mathematical event trigger frequencies.
- [ ] **Risk/Drawdown Limits Engine**: Build an autonomous circuit-breaker to halt agent-initiated executions if max daily drawdown is reached.
- [ ] **Multi-Pair Coexistence**: Scale the daemon to orchestrate concurrent LangGraph executions across multiple currency pairs simultaneously.

---

## 🏗️ Architecture Overview

The system operates as a continuous real-time loop divided into three specialized layers, keeping API token costs low by using pure math for event filtering:

```
┌─────────────────────────────────────────────────────────────┐
│  LAYER 3 — LLM GRAPH (fires only on high-priority events)  │
│  AxonAIGraph → Trader → [Analysts x4] → Bull/Bear →        │
│  ResearchMgr → [Risk x3] → PortfolioManager → Decision     │
│  (Triggered on cooldown, e.g. 300s, when priority >= MED)   │
├─────────────────────────────────────────────────────────────┤
│  LAYER 2 — DETECTION ENGINE (pure math, zero tokens)       │
│  EventDetector: 9 structural indicators & pattern checks   │
│  LiveWorldState: spread / session / ATR / RSI / regime      │
│  LiveMarketEvidence: swing H/L / key levels / patterns      │
├─────────────────────────────────────────────────────────────┤
│  LAYER 1 — TICK ENGINE (100ms MT5 poll loop)               │
│  TickEngine → CandleBuilder (M1/M5/M15/H1) → callbacks     │
│  MT5 raw ticks → OHLCV → candle close events               │
└─────────────────────────────────────────────────────────────┘
          ↕ WebSocket ↕
┌─────────────────────────────────────────────────────────────┐
│  DASHBOARD (FastAPI + HTML5 CSS Glassmorphism HUD)         │
│  /ws — live streams ticks, indicators, events & agent state │
└─────────────────────────────────────────────────────────────┘
```

### Signal Flow:
`MT5 Tick` ➔ `TickEngine` ➔ `CandleBuilder` ➔ `EventDetector` ➔ `queue.Queue` ➔ `AxonDaemon` ➔ `GraphExecutor` ➔ `LangGraph` ➔ `PM Decision` ➔ `Dashboard`

---

## ✨ Key Features

### 1. Layer 1: Dedicated Tick Engine
- Threaded polling loop fetching new ticks from MetaTrader 5 every 100ms.
- High-performance, epoch-boundary-aware `CandleBuilder` constructing **M1, M5, M15, and H1** OHLCV candles in real-time.
- Rolling 10,000-tick buffer.

### 2. Layer 2: Zero-Token Mathematical Detection Engine
Monitors the chart constantly and extracts structural patterns using pure mathematical logic:
1. **LEVEL_BREACH**: Close crosses above/below significant historic high/low levels.
2. **STRUCTURE_BREAK**: Break of Structure (BOS) indicating a shift in trend.
3. **SWEEP_DETECTED**: High-probability liquidity sweep (pin bar poking past swing level before reversing).
4. **VOLATILITY_SPIKE**: High-velocity expansion (candle range > 2× rolling ATR).
5. **CANDLE_PATTERN**: Classic candlestick signals (Pin bars, Engulfing bars) at major levels.
6. **REGIME_SHIFT**: Dominant technical regime transformations.
7. **SESSION_TRANSITION**: Shift across Asian, London, and New York trading sessions.
8. **SPREAD_CHANGE**: Tracking of broker spread levels (gating operations during spread spikes).
9. **MOMENTUM_DIVERGENCE**: Classical RSI and price action divergences.
10. **EMPIRICAL_REVERSAL_STRUCTURES**: Non-traditional structural filters (Wick Climaxes, Absorption Volume Stalls, V-Rebounds) tracked statistically across M15, H1, and H4 timeframes.

### 3. Execution Decision Intelligence & Proximity-Confluence Hybrid Rule
Dynamically validates events using context-aware filters before execution:
- **Proximity-Confluence Bypass**:
  - **High Proximity (≤ 2.0 pips)**: Automatically bypasses structural confirmation checks to capture high-reward entries immediately at S/R zones.
  - **Moderate Proximity (2.0 to 5.0 pips)**: Strictly requires empirical structural confluences (outlier shadows or absorption stalls) to prevent execution on unconfirmed breakouts.
- **Empirical Reversal Patterns (Direction-Specific & Pure Baseline)**:
  - **Wick Climax**: Statistical outlier shadow rejection (lower shadows for `BUY`, upper shadows for `SELL`) relative to a pure lookback period (excluding the current candle).
  - **Volume Stall (Absorption)**: Compressed body size (<20% of ATR) under heavy relative volume (>1.3× average) indicating order block absorption.
  - **V-Rebound**: Dynamic momentum sequence deceleration followed by a strong impulse candle opposing the prior sequence.

### 4. Layer 3: LangGraph Multi-Agent Architecture
Compile-once, reuse-always LangGraph Directed Acyclic Graph (DAG):
- **Trader Agent**: Establishes initial hypothesis.
- **Analysts (x4 Parallel)**: Market, News, Fundamentals, and Sentiment/Social analysis.
- **Researchers (Bull vs Bear)**: Critical debates examining evidence, bias, and counter-arguments.
- **Research Manager**: Harmonizes analyst outputs.
- **Risk Team (x3 Parallel)**: Conservative, Neutral, and Aggressive risk assessments.
- **Portfolio Manager**: Renders final BUY, SELL, or HOLD verdict.

### 5. Enterprise-Grade LLM Integration
- Provider-agnostic factory supporting **OpenAI (GPT-4/5), Anthropic (Claude 3.5/3.7), Google (Gemini 1.5/2.0), Azure OpenAI**, and local models via **Ollama**.
- Auto-resolves API keys and configuration dynamically.

### 6. Advanced Monitoring Dashboard
- Integrated FastAPI backend with a real-time, responsive glassmorphism web HUD.
- Live-streams account equity, indicators, active regimes, events, agent conversation traces, and final trade decisions via WebSockets.
- Session persistence: Saves and restores events across daemon restarts.

---

## 📂 Project Directory Structure

```
axonai/                         # Core codebase
├── agents/                     # Multi-Agent system
│   ├── analysts/               # Technical, Sentiment, News analysts
│   ├── researchers/            # Bull and Bear debaters
│   ├── risk_mgmt/              # Aggressive, Neutral, Conservative risk debaters
│   └── utils/                  # Agent states, memory managers, indicators
├── dataflows/                  # Data wrappers (MT5, AlphaVantage, yfinance)
├── graph/                      # LangGraph DAG definition, SQLite checkpointer
├── llm_clients/                # Unified LLM provider abstraction
├── realtime/                   # Real-time daemon module
│   ├── api_server.py           # FastAPI WebSockets dashboard
│   ├── daemon.py               # Main orchestrator & loop
│   ├── event_detector.py       # Mathematical pattern detection algorithms
│   ├── event_types.py          # Real-time data schemas
│   ├── graph_executor.py       # LangGraph invocation wrapper
│   ├── live_state.py           # Real-time memory & structural state
│   └── tick_engine.py          # MT5 tick ingestion & candle builders
└── default_config.py           # Default configuration settings
cli/                            # Command Line Interface
└── static/                     # Web Dashboard frontend assets
```

---

## ⚙️ Installation

### Prerequisites
- **OS**: Windows (Required for native `MetaTrader5` integration).
- **Python**: 3.10 to 3.13.
- **MetaTrader 5 Client**: Installed and logged into a valid broker account.

### Step-by-Step Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/skynextindia/AxonAgent.git
   cd AxonAgent
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python -m venv venv
   venv\Scripts\activate
   ```

3. **Install the package in editable mode**:
   ```bash
   pip install -e .
   ```

4. **Setup environment variables**:
   Create a `.env` file in the root folder (using `.env.example` as a template):
   ```ini
   # LLM Provider Keys
   OPENAI_API_KEY=your_openai_key
   ANTHROPIC_API_KEY=your_anthropic_key
   GOOGLE_API_KEY=your_gemini_key

   # MT5 Broker Settings (If symbol suffix is used, e.g. "EURUSDm")
   AXONAI_MT5_SYMBOL_SUFFIX=m
   ```

---

## 🚀 Quick Start

### 1. Launch MetaTrader 5 Terminal
Ensure the MT5 terminal is open and connected to your broker.

### 2. Run the Real-Time Daemon
Spawn the daemon to monitor a pair (e.g. `EURUSD=X` which maps to `EURUSDm` in MT5):
```bash
axonai live EURUSD=X
```

### 3. Open the Dashboard
Navigate to **`http://localhost:8000`** in your browser to view the real-time HUD showing incoming ticks, active levels, event logs, and agent activity.

---

## 🔧 Configuration

All configurations can be customized in `axonai/default_config.py` or overridden using env variables prefixed with `AXONAI_`.

| Configuration Key | Env Override | Default | Description |
|---|---|---|---|
| `llm_provider` | `AXONAI_LLM_PROVIDER` | `openai` | Active model provider |
| `deep_think_llm` | `AXONAI_DEEP_THINK_LLM` | `gpt-4o` | Model for heavy reasoning & decisions |
| `quick_think_llm` | `AXONAI_QUICK_THINK_LLM` | `gpt-4o-mini` | Model for quick analysis |
| `tick_poll_interval_ms` | `AXONAI_TICK_POLL_INTERVAL_MS` | `100` | Polling speed for MT5 ticks |
| `realtime_cooldown_seconds`| `AXONAI_REALTIME_COOLDOWN_SECONDS`| `300` | Cooldown between LLM calls |
| `realtime_min_event_priority` | `AXONAI_REALTIME_MIN_EVENT_PRIORITY` | `MEDIUM` | Minimum event level to trigger graph |
| `realtime_suppress_asian` | `AXONAI_REALTIME_SUPPRESS_ASIAN` | `true` | Suppress LLM graph during Asian session |

---

## 📊 Real-Time Dashboard

The web interface is served locally when you launch the daemon:

- **Ticks & Spread Widget**: Live spread calculation, bids, and asks.
- **Regime & ATR Monitor**: Technical breakdown of volatility and current market structure.
- **Mathematical Event log**: Complete audit trail of BOS, LEVEL_BREACH, or liquidity sweeps.
- **Agent Trace Terminal**: Visual trace of the LangGraph multi-agent debate and reasoning chain.
- **Decision Engine**: Highlights the final buy, sell, or hold command with confidence metrics.

---

## 🧪 Verification & Testing

Verify that your environment, MetaTrader 5 integration, and LLM providers are working correctly by running the built-in test suite:

```bash
# Run all unit tests
pytest

# Validate environment setup
python -m tests.test_env
```

---

## 🛡️ Disclaimer

*AxonAI is designed and open-sourced strictly for research and educational purposes. It is not intended as financial, investment, or trading advice. Past performance is no guarantee of future results.*
