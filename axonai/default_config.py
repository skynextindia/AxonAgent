import os

_AXONAI_HOME = os.path.join(os.path.expanduser("~"), ".axonai")

# Single source of truth for env-var → config-key overrides. To expose
# a new config key for environment-based override, add a row here — no
# entry-point script changes required. Coercion is driven by the type
# of the existing default, so users can keep writing plain strings in
# their .env file.
_ENV_OVERRIDES = {
    "AXONAI_LLM_PROVIDER":         "llm_provider",
    "AXONAI_DEEP_THINK_LLM":       "deep_think_llm",
    "AXONAI_QUICK_THINK_LLM":      "quick_think_llm",
    "AXONAI_LLM_BACKEND_URL":      "backend_url",
    "AXONAI_OUTPUT_LANGUAGE":      "output_language",
    "AXONAI_MAX_DEBATE_ROUNDS":    "max_debate_rounds",
    "AXONAI_MAX_RISK_ROUNDS":      "max_risk_discuss_rounds",
    "AXONAI_CHECKPOINT_ENABLED":   "checkpoint_enabled",
    "AXONAI_BENCHMARK_TICKER":     "benchmark_ticker",
    "AXONAI_INTRADAY_INTERVAL":    "intraday_interval",
    "AXONAI_MT5_TERMINAL_PATH":    "mt5_terminal_path",
    "AXONAI_MT5_SYMBOL_SUFFIX":    "mt5_symbol_suffix",
    "AXONAI_REALTIME_MAGIC_NUMBER": "realtime_magic_number",
    "AXONAI_REALTIME_DEFAULT_LOT_SIZE": "realtime_default_lot_size",
    "AXONAI_REALTIME_DEVIATION": "realtime_deviation",
    "AXONAI_REALTIME_MIN_CONFLUENCE_CONDITIONS": "realtime_min_confluence_conditions",
    "AXONAI_REALTIME_DRY_RUN": "realtime_dry_run",
}



def _coerce(value: str, reference):
    """Coerce env-var string to the type of the existing default value."""
    if isinstance(reference, bool):
        return value.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(config: dict) -> dict:
    """Apply AXONAI_* env vars to the config dict in-place."""
    for env_var, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        config[key] = _coerce(raw, config.get(key))
    return config


DEFAULT_CONFIG = _apply_env_overrides({
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("AXONAI_RESULTS_DIR", os.path.join(_AXONAI_HOME, "logs")),
    "data_cache_dir": os.getenv("AXONAI_CACHE_DIR", os.path.join(_AXONAI_HOME, "cache")),
    "memory_log_path": os.getenv("AXONAI_MEMORY_LOG_PATH", os.path.join(_AXONAI_HOME, "memory", "trading_memory.md")),
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings
    "llm_provider": "deepseek",
    "deep_think_llm": "deepseek-reasoner",
    "quick_think_llm": "deepseek-chat",
    # When None, each provider's client falls back to its own default endpoint.
    "backend_url": None,
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "English",
    # Intraday interval timeframe (e.g. 1d, 1h, 15m, 5m)
    "intraday_interval": "1d",

    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    "analyst_concurrency_limit": 1,
    # News / data fetching parameters
    # Increase for longer lookback strategies or to broaden macro coverage;
    # decrease to reduce token usage in agent prompts.
    "news_article_limit": 20,             # max articles per ticker (ticker-news)
    "global_news_article_limit": 10,      # max articles for global/macro news
    "global_news_lookback_days": 7,       # macro news lookback window
    # Search queries used by get_global_news for macro headlines. Extend or
    # replace to broaden geographic / sector coverage.
    "global_news_queries": [
        "US Dollar Federal Reserve interest rates inflation CPI NFP",
        "Euro ECB interest rate inflation GDP Eurozone",
        "Pound Bank of England BOE interest rate GDP CPI",
        "Japanese Yen Bank of Japan BOJ policy rate intervention",
        "global central banks monetary policy rates divergence",
    ],
    # Data vendor configuration
    # Category-level configuration (default for all tools in category)
    "data_vendors": {
        "core_stock_apis": "yfinance",       # Options: alpha_vantage, yfinance
        "technical_indicators": "yfinance",  # Options: alpha_vantage, yfinance
        "fundamental_data": "yfinance",      # Options: alpha_vantage, yfinance
        "news_data": "yfinance",             # Options: alpha_vantage, yfinance
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
    # Benchmark for alpha calculation in the reflection layer.
    # ``benchmark_ticker`` (when set) overrides the suffix map for all
    # tickers; leave it None to use ``benchmark_map`` for auto-detection
    # based on the ticker's exchange suffix. SPY remains the US default
    # so the reflection label keeps reading "Alpha vs SPY" for US tickers
    # while non-US tickers get their regional index automatically.
    "benchmark_ticker": None,
    "benchmark_map": {
        ".NS":  "^NSEI",    # NSE India (Nifty 50)
        ".BO":  "^BSESN",   # BSE India (Sensex)
        ".T":   "^N225",    # Tokyo (Nikkei 225)
        ".HK":  "^HSI",     # Hong Kong (Hang Seng)
        ".L":   "^FTSE",    # London (FTSE 100)
        ".TO":  "^GSPTSE",  # Toronto (TSX Composite)
        ".AX":  "^AXJO",    # Australia (ASX 200)
        "=X":   "DX-Y.NYB", # Forex (US Dollar Index)
        "":     "SPY",      # default for US-listed tickers (no suffix)
    },
    # MetaTrader 5 integration
    "mt5_terminal_path": None,      # None = auto-detect; or full path to terminal64.exe
    "mt5_symbol_suffix": "m",       # Broker symbol suffix: EURUSD → EURUSDm
    "mt5_timeframes": ["M15", "H1", "H4", "D1"],  # Multi-TF analysis order
    # ── Real-time daemon settings ────────────────────────────────────────
    "realtime_enabled": False,
    "tick_poll_interval_ms": 100,
    "realtime_cooldown_seconds": 10,
    "realtime_min_event_priority": "MEDIUM",
    "realtime_tick_buffer_size": 10_000,
    "realtime_candle_history": 500,
    "realtime_suppress_asian": False,
    "realtime_level_reset_atr_multiple": 2.0,
    "realtime_log_events": True,
    "realtime_magic_number": 123456,
    "realtime_default_lot_size": 0.01,
    "realtime_deviation": 20,
    "realtime_min_confluence_conditions": 1,
    "realtime_dry_run": True,
    "peak_detector_rule_c_enabled": False,
    "trade_risk_pct": 0.01,
})
