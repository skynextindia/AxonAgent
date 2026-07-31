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
    "AXONAI_CORR_REQUIRE_USD_ALIGNMENT": "corr_require_usd_alignment",
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
    # End-of-day handling (all times UTC / DST-independent — IST has no DST):
    #  * eod_entry_cutoff_utc (17:30 UTC = 23:00 IST): stop opening NEW positions.
    #    Open trades are HELD and left to the engine's own exits (trailing/SL/TP)
    #    — NOT force-closed here.
    #  * eod_flatten_before_close_min before the NY 5pm rollover (DST-aware,
    #    ~20:55 UTC = ~02:25 IST): force-flat ALL remaining positions once, so
    #    nothing is carried into the daily market close.
    #  * eod_resume_utc (00:30 UTC = 06:00 IST): reset the trading day and resume
    #    new entries. The no-new-entry window wraps past midnight UTC.
    "eod_hard_flat_enabled": True,
    "eod_entry_cutoff_utc": 17.5,               # 23:00 IST — no NEW entries after this
    "eod_flatten_before_close_min": 5,          # flatten this many min before the NY 5pm rollover
    "eod_resume_utc": 0.5,                       # 06:00 IST — reset day + resume entries
    # Legacy session-transition profit-close is superseded by the pre-rollover
    # flatten above (which holds winners too until the daily close); keep it off.
    "eod_close_enabled": False,
    "realtime_level_reset_atr_multiple": 2.0,
    "realtime_log_events": True,
    "realtime_magic_number": 123456,
    "realtime_default_lot_size": 0.01,
    # Minimum executable lot for EVERY pair: dynamic risk-sizing scales UP from
    # here and is never allowed below it (nor below it after correlation scaling).
    # NOTE: a 1.0-lot floor overrides the risk-% target on small accounts — e.g.
    # EURUSD 1.0 lot with a 16-pip stop risks ~$160 (~1.6% of a $10k account).
    "realtime_min_lot": 1.0,
    "realtime_deviation": 20,
    "realtime_min_confluence_conditions": 1,
    "realtime_dry_run": True,
    # Run trailing-stop management + closed-position (SL/TP) detection in live
    # mode too — required for live trailing stops, post-trade cooldowns, and the
    # per-pair SL lockout. Turn off to revert to dry-run-only monitoring.
    "realtime_manage_positions_live": True,
    # --- Entry quality gates (both default OFF; soak in dry-run first) ---
    # Falling-knife filter: veto a BUY whose M15 trigger candle closed below its
    # open. Validated 2026-07-30 over 197 trades (2026-06-15..07-30): such BUYs
    # net -2.0 pips/trade, robust out-of-sample (June & July both negative) and
    # on both symbols. Skipping them lifted net +37% and win-rate +3.8pts.
    "entry_skip_falling_knife": False,
    # Directional BUY-side skips (config-gated, default OFF). OOS hunt over
    # 2026-06 (out-of-sample) and 2026-07 (in-sample) found BUYs net-NEGATIVE in
    # BOTH months while SELLs carried the entire edge (+103 / +340 pips). The two
    # worst BUY pockets held on both months: panic-regime BUYs (-58 / -30) and
    # active-session BUYs 08-16 UTC (-68 / -74).
    # NOTE: the window is 08-16 UTC, bucketed on TRUE UTC. It was previously
    # 07-12 ("London"), derived from local timestamps mistaken for UTC+3 — wrong
    # by 3 hours. That window skipped hour 07 (net-POSITIVE in both months,
    # +28 / +29) and missed the negative 12-16 block, leaving the combined filter
    # worth only +38 / +1. On the corrected clock it is worth +142 combined.
    # Caveat: still partly a directional bet (both months shared a down-trend),
    # so validate on August live before trusting it wider.
    #   entry_skip_panic_buy   : skip BUY when dominant_regime == "panic"
    #   entry_skip_session_buy : skip BUY inside the active-session window (UTC)
    #   entry_skip_all_buy     : suppress ALL BUY entries (full directional bet)
    "entry_skip_panic_buy": False,
    "entry_skip_session_buy": False,
    "entry_skip_session_buy_start": 8,
    "entry_skip_session_buy_end": 16,
    "entry_skip_all_buy": False,
    # No-progress abort: if an open position has not reached
    # noprogress_abort_min_favorable_pips of favorable excursion within
    # noprogress_abort_minutes of entry, scratch it at market. Catches
    # "wrong-from-entry" trades (MFE ~ 0) before they ride to a full stop; the
    # entry filter above only reaches BUYs, this reaches either side. The minutes
    # threshold is not derivable from history (MAE/MFE give magnitude, not
    # timing) — tune it in dry-run.
    "entry_noprogress_abort": False,
    "noprogress_abort_minutes": 12.0,
    "noprogress_abort_min_favorable_pips": 2.0,
    # The abort closes REAL positions, so it can't be soaked the way the entry
    # filter can. When True (the default even once the abort is enabled) it only
    # LOGS the position it would scratch and leaves it open — so you can watch
    # it decide on the live account before arming it. Set False to actually close.
    "noprogress_abort_notice_only": True,
    # Trailing-stop distance override (× ATR). None = use the per-pair
    # trail_dist_atr_mult (0.35). Set (e.g. 1.0) to widen the trail for ALL pairs
    # without editing the spec — the daemon reads this in _manage_trailing_stops.
    # M1-replay over 201 trades: widening 0.35 → 1.0 lifted net ~+40% with the SAME
    # win rate (trail distance sets capture, not win/loss) and beat 0.35 in both
    # out-of-sample halves. Applies to lead AND node. Soak on demo before trusting.
    "trail_dist_atr_mult_override": None,
    # Hard SL/TP pip ceiling (config-gated; default OFF). When True, the executor
    # caps BOTH the stop and the target at the per-pair ``max_stop_pips`` (USDJPY
    # 10, EURUSD 16) no matter how large 2×ATR or the vol-driven floor grows — so
    # the stop can never balloon to the 100+ pip widths a high-ATR session
    # produces. NOTE: a 10-pip USDJPY stop sits inside normal M1 noise, so expect
    # a much higher stop-out rate; and with a capped target the ATR-scaled trail
    # (breakeven at 0.60×ATR ≈ 49 USDJPY pips) can't arm before TP is hit.
    "enforce_max_stop_pips": False,
    # Exec-node lot mirroring (config-gated, node-only; default OFF). When set, the
    # node sizes each routed entry to this multiple of the LEAD's executed lot
    # (e.g. 10.0 → node trades 10× the Eightcap lot), overriding the node's own
    # risk-based sizing and the correlation size_scale. Still clamped to
    # ``exec_node_max_lot`` and then trimmed by the per-trade/combined risk caps.
    "exec_node_lot_multiple": None,
    # Per-trade risk override (fraction of equity; default None → use the per-pair
    # spec risk_pct, 0.01). When set (e.g. 0.019), EVERY pair sizes each entry to
    # risk this share of account equity at its stop distance — overrides the
    # SYMBOL_CALIBRATION risk_pct on both terminals. With the tight stop caps this
    # sizes UP (a 16-pip EURUSD stop at 1.9% ≈ 1.2 lead lots / ~12 node lots; a
    # 10-pip USDJPY stop at 1.9% ≈ 3 lead lots — high notional/leverage, watch
    # broker margin).
    "realtime_risk_pct_override": None,
    # Supervisor watchdog: if a pair's daemon thread dies, always alert loudly.
    # Set True to ALSO flatten that pair's positions (they keep their entry SL
    # regardless, so this is an opt-in extra safety net, not a requirement).
    "supervisor_flatten_on_thread_death": False,
    # Cross-pair correlation engine (multi-pair only; see correlation_engine.py).
    "corr_engine_enabled": True,
    "corr_lead_symbol": "EURUSD",       # the pair that trades ungated; others follow
    "corr_window_bars": 100,            # H1 bars for rolling correlation
    "corr_refresh_seconds": 300,        # how often to recompute corr / bias / vol
    "corr_max_net_usd": 200000.0,       # cap on combined net-USD exposure across pairs
    "corr_bias_lookback_bars": 10,      # H1 bars for the lead-pair bias
    "corr_veto_bias_threshold": 0.0015, # |lead return| that vetoes a contradicting entry
    "corr_size_scale_min": 0.25,        # floor for correlation/exposure size scaling
    "corr_require_usd_alignment": True, # dollar-direction lock: while any position is open,
                                        # a new entry (lead OR follower) must agree on USD
                                        # direction with every open position (never trade the
                                        # negatively-correlated pairs against each other)
    # ── Range extreme gate (reject wrong-end entries) ─────────────────────────
    # Block SELLs fired near the BOTTOM of the recent range (selling into
    # support) and BUYs near the TOP (buying into resistance). Measured against
    # the prior N closed M15 candles (live_evidence._m15_candles, seeded from
    # ~10 days of history at init so it is valid immediately after a restart) —
    # NOT the tiny currently-forming candle the old gate used.
    "range_gate_lookback": 20,          # closed M15 candles that define the range (~5h)
    "range_gate_edge": 0.25,            # block SELL if pos < edge (lower quarter = support);
                                        # block BUY if pos > 1-edge (upper quarter = resistance)
    "peak_detector_rule_c_enabled": False,
    "trade_risk_pct": 0.01,
    "realtime_use_pinpoint_price": False,
    "realtime_correct_rule_a_direction": False,
    "realtime_cooldown_bypass_better_peak": False,
    "indicator_rsi_length": 14,
    "indicator_ema_fast": 12,
    "indicator_ema_slow": 26,
    # ── A→B order mirroring / execution node (default OFF) ─────────────────────
    # The lead ("brain") forwards each entry/close DECISION (never a price) to a
    # second, execution-only terminal, which re-sizes to its OWN equity and
    # resolves its OWN broker ticker/pip. See mirror_client.py + exec_node.py.
    "mirror_enabled": False,                 # lead side: forward decisions to the exec node
    "mirror_url": "ws://127.0.0.1:8770",     # exec-node inbound WebSocket URL
    "exec_node_mode": False,                 # this process is an execution node (detection OFF)
    # Suffix appended to this process's report/state filenames. The lead and the
    # execution node run from the SAME working directory, so an untagged path
    # means two OS processes appending to one file: torn writes, and two
    # different accounts' P&L merged into one stream with no way to tell them
    # apart. Set by run.py ("" for the lead, "_node" for the exec node).
    "instance_tag": "",
    "exec_node_max_lot": 12.0,               # exec node: per-pair lot ceiling (headroom for 10× lead mirroring)
    # Prop-account risk ceilings (config-gated; None = off). Numbers are percent.
    # per_trade: no single trade's stop-risk may exceed this % of equity (the lot
    # is trimmed to fit). combined: total open stop-risk across ALL positions may
    # not exceed this % (a new entry is shrunk to the remaining budget, or blocked
    # if even min_lot won't fit). Trailed-to-breakeven positions free up budget.
    "risk_cap_per_trade_pct": None,          # e.g. 1.5  → single-trade stop-risk ≤ 1.5%
    "risk_cap_combined_pct": None,           # e.g. 2.5  → total open stop-risk ≤ 2.5%
    "exec_node_magic_offset": 500000,        # exec node: distinct magic = lead magic + offset
    # ── Mirror replay + reconcile (lead side) ─────────────────────────────────
    # Without these the mirror is pure fire-and-forget: any decision made while
    # the node is down is lost forever and the two accounts silently diverge.
    "mirror_queue_max": 200,                 # bounded replay queue (oldest dropped past this)
    # Entries EXPIRE, closes never do. The edge is a microstructure reversal that
    # is gone in seconds, so replaying a minutes-old entry is not "catching up" —
    # it opens a brand-new unvetted trade at a price the lead never signalled on.
    "mirror_entry_ttl_seconds": 45.0,
    # Reconcile on reconnect is asymmetric on purpose. A position the node holds
    # and the lead does not is ALWAYS closed (unmanaged risk). A position the lead
    # holds and the node does not is only ALERTED — filling it late means buying a
    # move that already happened, possibly one the lead is about to exit. Flat is
    # fine on a challenge account; wrong-footed is not. Turn this on to fill anyway.
    "mirror_reconcile_enter": False,
    # ── Prop-firm compliance guard (default OFF; see risk_guard.py) ───────────
    # Adds the two limits a funded account is killed by and which the plain
    # daily breaker does NOT model: an OVERALL drawdown floor measured from the
    # initial balance, and a daily limit measured on BROKER SERVER days. When a
    # limit is breached the guard both blocks new entries AND flattens open
    # positions (a floating loss running past the line is the usual way an
    # account dies). Defaults match FundingPips 2-Step Standard.
    "prop_guard_enabled": False,             # enable per-account (funded terminals only)
    "prop_initial_balance": None,            # None → seed from the account's first balance
    "prop_max_drawdown_pct": 10.0,           # overall floor from initial balance
    "prop_max_drawdown_trailing": False,     # False = static floor; True = ratchets to equity highs
    "prop_daily_loss_pct": 5.0,              # daily loss limit (server day)
    # Trip at (100 - buffer)% of each limit so the real breach line is never
    # touched: 20% buffer → daily trips at 4% and the 10% floor trips at 8%.
    "prop_safety_buffer_pct": 20.0,
    "prop_flatten_on_breach": True,          # close open positions on breach, not just block
    "prop_state_file": "reports/prop_guard.json",
    # ── Consistency rule (payout gate; NOT a hard breach) ──────────────────────
    # FundingPips 2-Step Pro forbids any single trading day from exceeding 45%
    # of total realized profit. Unlike a DD breach, tripping this does NOT kill
    # the account — but it BLOCKS the payout. Enforcement here BLOCKS NEW
    # ENTRIES for the day if today's realized would push the ratio past the
    # (buffered) threshold; open positions are left alone (flattening would
    # only ADD to today's realized and make the ratio worse).
    "prop_consistency_pct": 45.0,            # firm's rule; 0 = disabled
    # Profit-target notice (informational, not a halt):
    "prop_profit_target_pct": 6.0,           # 2-Step Pro Phase 1; 0 = disabled
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
        "max_lot": 2.0,              # hard safety ceiling; 1% risk sizes to ~0.6 lot
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 2.0,
        "min_stop_pips": 16.0,
        "max_stop_pips": 16.0,       # hard SL/TP ceiling when enforce_max_stop_pips=True
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
        "max_lot": 2.0,              # hard safety ceiling; 1% risk sizes to ~1.0 lot
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 2.0,
        "min_stop_pips": 16.0,
        "max_stop_pips": 10.0,       # hard SL/TP ceiling when enforce_max_stop_pips=True
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
        if base.get("realtime_risk_pct_override") is not None:
            cfg["realtime_risk_pct"] = float(base["realtime_risk_pct_override"])
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
    _rpo = base.get("realtime_risk_pct_override")
    if _rpo is not None:
        cfg["realtime_risk_pct"] = float(_rpo)
    cfg["pip_size"] = spec.get("pip_size", auto_pip)
    # None => compute dynamically at order time (USD-base pairs like USDJPY).
    cfg["realtime_pip_value_per_lot"] = spec.get(
        "pip_value_per_lot", base.get("realtime_pip_value_per_lot", 10.0)
    )
    cfg["sl_atr_mult"] = spec.get("sl_atr_mult", 2.0)
    cfg["tp_atr_mult"] = spec.get("tp_atr_mult", 2.0)
    cfg["min_stop_pips"] = spec.get("min_stop_pips", 16.0)
    cfg["max_stop_pips"] = spec.get("max_stop_pips", base.get("max_stop_pips"))
    cfg["be_atr_mult"] = spec.get("be_atr_mult", 0.60)
    cfg["trail_trigger_atr_mult"] = spec.get("trail_trigger_atr_mult", 0.80)
    cfg["trail_dist_atr_mult"] = spec.get("trail_dist_atr_mult", 0.35)
    return cfg
