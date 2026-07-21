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
    "AXONAI_MT5_LOGIN":            "mt5_login",
    "AXONAI_MT5_PASSWORD":         "mt5_password",
    "AXONAI_MT5_SERVER":           "mt5_server",
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
    "mt5_terminal_path": "C:/Program Files/Eightcap Global MT5 Terminal/terminal64.exe",
    "mt5_symbol_suffix": "none",    # Broker symbol suffix: EURUSD.i (no suffix needed)
    "mt5_login": None,              # Account number (int)
    "mt5_password": None,           # Account password (str)
    "mt5_server": None,             # Account server name (str)
    "mt5_timeframes": ["M15", "H1", "H4", "D1"],  # Multi-TF analysis order
    # ── Real-time daemon settings ────────────────────────────────────────
    "realtime_enabled": False,
    "tick_poll_interval_ms": 100,
    "realtime_cooldown_seconds": 10,
    "realtime_min_event_priority": "MEDIUM",
    "realtime_tick_buffer_size": 10_000,
    "realtime_candle_history": 500,
    "realtime_suppress_asian": False,
    # Sessions in which new entries are allowed (used when auto mode is OFF).
    # Valid: "asian", "london", "overlap", "newyork", "rollover".
    "realtime_active_sessions": ["asian", "london", "overlap", "newyork", "rollover"],
    # Self-configuring session selector: learns per-session movement of the pair
    # and enables only the sessions worth trading. Falls back to the manual list
    # above until enough samples are collected.
    "realtime_auto_sessions": False,
    "realtime_auto_sessions_window": 10,        # rolling days of history per session
    "realtime_auto_sessions_min_samples": 3,    # samples needed before auto kicks in
    "realtime_auto_sessions_spread_mult": 25.0, # range must be >= this * avg spread
    "realtime_auto_sessions_rel_floor": 0.40,   # range must be >= this * best session
    # EOD hard-flat: close ALL positions near the NY liquidity-end each trading day
    # and bar re-entry through the tight pre-close window.
    "eod_hard_flat_enabled": True,
    "eod_hard_flat_minutes_before": 10,         # minutes before ny_close to flatten
    "realtime_level_reset_atr_multiple": 2.0,
    "realtime_log_events": True,
    "realtime_magic_number": 123456,
    "realtime_default_lot_size": 0.01,
    "realtime_deviation": 20,
    "realtime_min_confluence_conditions": 1,
    "realtime_dry_run": True,
    # Run trailing-stop management + closed-position (SL/TP) detection in live
    # mode too — required for live trailing stops, post-trade cooldowns, and the
    # per-pair SL lockout. Turn off to revert to dry-run-only monitoring.
    "realtime_manage_positions_live": True,
    "peak_detector_rule_c_enabled": False,
    "trade_risk_pct": 0.01,
    "realtime_use_pinpoint_price": False,
    "realtime_correct_rule_a_direction": False,
    "realtime_cooldown_bypass_better_peak": False,
    "indicator_rsi_length": 14,
    "indicator_ema_fast": 12,
    "indicator_ema_slow": 26,
})


# ── Per-pair calibration ─────────────────────────────────────────────────────
# The daemon runs one instance per symbol. SYMBOL_CALIBRATION overlays
# pair-specific values onto the flat DEFAULT_CONFIG via resolve_symbol_config(),
# which maps each spec onto the ``realtime_*`` keys the trade executor and daemon
# already read — so downstream components need no changes. A symbol not listed
# here falls back to DEFAULT_CONFIG values plus an auto-derived pip size.
#
# magic_number MUST be distinct per pair: the executor's position-conflict guard
# filters open positions by magic, so a shared magic would cross-block pairs.
SYMBOL_CALIBRATION = {
    "EURUSD": {
        "magic_number": 123457,      # preserves the historical EURUSD magic
        "pip_size": 0.0001,
        "pip_value_per_lot": 10.0,   # USD-quote pair: ~$10 / pip / 1.0 lot
        "risk_pct": 0.01,
        "max_lot": 0.10,
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 2.0,
        "min_stop_pips": 16.0,
        "be_atr_mult": 0.60,
        "trail_trigger_atr_mult": 0.80,
        "trail_dist_atr_mult": 0.35,
    },
    "USDJPY": {
        "magic_number": 123458,      # distinct from EURUSD
        "pip_size": 0.01,
        # USD-BASE pair: $/pip/lot is price-dependent (~contract*pip/price ≈ $6–7),
        # not a constant. None → compute dynamically at order time from live price.
        "pip_value_per_lot": None,
        "risk_pct": 0.01,
        "max_lot": 0.10,
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 2.0,
        "min_stop_pips": 16.0,
        "be_atr_mult": 0.60,
        "trail_trigger_atr_mult": 0.80,
        "trail_dist_atr_mult": 0.35,
    },
}


def _canonical_symbol(symbol: str) -> str:
    """Bare 6-letter pair from any broker/yfinance form.

    'EURUSDm' / 'EURUSD.i' / 'EURUSD=X' → 'EURUSD'.
    """
    letters = "".join(ch for ch in (symbol or "").upper() if ch.isalpha())
    return letters[:6] if len(letters) >= 6 else letters


def resolve_symbol_config(base: dict, symbol: str) -> dict:
    """Return a copy of *base* with per-symbol calibration applied.

    Maps the SYMBOL_CALIBRATION spec onto the ``realtime_*`` keys the trade
    executor and daemon already read, so no downstream code changes are needed.
    Also repairs the historical dead-key bug: the executor reads
    ``realtime_risk_pct`` while the flat config only defined ``trade_risk_pct``.
    Unlisted symbols get sensible auto-derived defaults (JPY/XAU → 0.01 pip).
    """
    cfg = dict(base)
    canon = _canonical_symbol(symbol)
    spec = SYMBOL_CALIBRATION.get(canon)

    # Auto-derived pip size fallback (JPY/XAU quote → 0.01, else 0.0001).
    auto_pip = 0.01 if ("JPY" in canon or "XAU" in canon) else 0.0001

    if spec is None:
        cfg.setdefault("pip_size", auto_pip)
        cfg["realtime_risk_pct"] = base.get(
            "realtime_risk_pct", base.get("trade_risk_pct", 0.01)
        )
        # Sensible pip-value default for unlisted pairs: USD-quote (XXXUSD) ≈ $10;
        # USD-base / crosses → None so the executor derives it from the live price.
        if "realtime_pip_value_per_lot" not in cfg:
            cfg["realtime_pip_value_per_lot"] = 10.0 if canon.endswith("USD") else None
        return cfg

    cfg["realtime_magic_number"] = spec.get(
        "magic_number", base.get("realtime_magic_number", 123456)
    )
    cfg["realtime_max_lot"] = spec.get("max_lot", base.get("realtime_max_lot", 0.10))
    cfg["realtime_risk_pct"] = spec.get("risk_pct", base.get("trade_risk_pct", 0.01))
    cfg["pip_size"] = spec.get("pip_size", auto_pip)
    # None => compute dynamically at order time (USD-base pairs like USDJPY).
    cfg["realtime_pip_value_per_lot"] = spec.get(
        "pip_value_per_lot", base.get("realtime_pip_value_per_lot", 10.0)
    )
    cfg["sl_atr_mult"] = spec.get("sl_atr_mult", 2.0)
    cfg["tp_atr_mult"] = spec.get("tp_atr_mult", 2.0)
    cfg["min_stop_pips"] = spec.get("min_stop_pips", 16.0)
    cfg["be_atr_mult"] = spec.get("be_atr_mult", 0.60)
    cfg["trail_trigger_atr_mult"] = spec.get("trail_trigger_atr_mult", 0.80)
    cfg["trail_dist_atr_mult"] = spec.get("trail_dist_atr_mult", 0.35)
    return cfg
