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
    # LOWERED 1.0->0.1 2026-08-13 (user): the 1.0 floor is what forced yesterday's
    # SL-distance shrink (kept a 1-lot lead at 1.1% by tightening the stop). With the
    # real 20/30-pip stops restored, 1.1% of the ~10k lead = 0.55/0.58 lot, so the
    # floor must sit below that. Node lots (5.5+) never touch this floor.
    "realtime_min_lot": 0.1,
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
    # ── Hard-distance risk mode (config-gated; default OFF) ────────────────────
    # When True, the executor and the trailing manager ignore ATR entirely and use
    # FIXED, session-independent pip distances per pair: SL = TP = trailing distance
    # = ``realtime_hard_stop_pips`` (mapped from SYMBOL_CALIBRATION hard_stop_pips —
    # EURUSD 20, USDJPY 30). This SUPERSEDES the 2×ATR term, the min_stop floor,
    # the correlation vol-ratio floor, AND enforce_max_stop_pips. TP is symmetric
    # 1:1 with SL, so a trade exits at ±hard_stop_pips (or at breakeven once the
    # ATR breakeven trigger arms). Applies to BOTH the lead and the exec node.
    "hard_distance_mode": False,
    # Per-pair hard SL/TP distance in pips (filled by resolve_symbol_config from
    # SYMBOL_CALIBRATION.hard_stop_pips). None → no pair-specific distance, so
    # hard_distance_mode falls back to the ATR path even when the flag is on.
    "realtime_hard_stop_pips": None,
    # Per-pair hard TRAIL distance in pips (from SYMBOL_CALIBRATION.hard_trail_pips).
    # When set TIGHTER than hard_stop_pips, the trailing stop locks profit BEFORE
    # the equal-distance TP is reached (with a 1:1 TP=SL, an equal trail is dormant
    # because TP always fires first). None → the trail falls back to the stop
    # distance (the old 1:1-dormant behaviour). Only the trail uses this; SL and TP
    # stay at hard_stop_pips, so per-trade risk is unchanged.
    "realtime_hard_trail_pips": None,
    # Fixed-lot sizing (config-gated; default None → OFF). When set (e.g. the lead
    # runs 1.0), EVERY entry uses EXACTLY this lot, bypassing risk-% sizing, the
    # correlation size_scale, and exec-node lot mirroring. Used on the Eightcap lead
    # so its size is deterministic regardless of session/ATR.
    "fixed_lot": None,
    # Max-loss budget sizing (config-gated; default None → OFF). When set (e.g. the
    # node runs 1800), each entry's lot is derived so a full stop-out loses at most
    # this many USD: lot = max_loss_usd / (sl_pips × $/pip/lot), clamped to
    # [realtime_min_lot, realtime_max_lot]. Bypasses risk-% sizing / size_scale /
    # lot-mirroring; the combined risk cap can still trim it further. Used on the
    # FundingPips node so per-trade loss is a hard dollar ceiling, not an equity %.
    "max_loss_per_trade_usd": None,
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
    # ── Direction-aware S/R selection (reject wrong-side fades) ────────────────
    # When True, the peak gate only considers S/R levels in the trade's PROFIT
    # direction (resistance at/above for SELL, support at/below for BUY) — it
    # never fades INTO a level. Set per-pair via SYMBOL_CALIBRATION. Default OFF.
    "direction_aware_sr": False,
    # ── Retest-confirmation veto (skip a fade that goes straight-against) ──────
    # DIAGNOSIS-VALIDATED (2026-08-08): the engine fires at the first single-tick
    # deceleration, so it fades pauses in live trends; ~72% of realised loss comes
    # from fades that go straight against (early MFE ~0, run to the hard stop). This
    # veto watches the first `retest_window_min` of a fade: if favorable displacement
    # reaches +retest_x_pips BEFORE adverse reaches retest_adverse_cap_pips it CONFIRMS;
    # if adverse hits first (or neither by the window) it is a straight-against VETO.
    # SHADOW logs the verdict without acting; ENABLED (per-pair, EURUSD-only) scratches
    # the vetoed position early. Defaults keep live behaviour UNCHANGED until armed.
    "retest_confirm_enabled": False,     # act on the veto (scratch straight-against fades)
    "retest_confirm_shadow": True,       # log the verdict without acting
    "retest_x_pips": 2.0,                # favorable pull-away that CONFIRMS the fade
    "retest_adverse_cap_pips": 2.0,      # adverse move that marks a straight-against VETO
    "retest_window_min": 30,             # minutes to resolve confirm vs veto

    # ── Staged (confirmation-by-degree) entry — probe small, add on confirmation ──
    # REPLAY-VALIDATED 2026-08-13 (n=83 natural-exit trades, Jul30-Aug13, commissions
    # ignored; memory confirmation-by-degree-replay). Instead of full size on the first
    # exhaustion tick, enter a PROBE (stage_probe_frac) and ADD the rest (stage_add_frac)
    # only once the fade pulls +stage_confirm_pips favorable within stage_add_window_min.
    # Total risk is UNCHANGED (probe+add = 1.0). The edge is LEFT-TAIL TRUNCATION: fades
    # that go straight-against never confirm → they ride at only the probe fraction, so the
    # big trend-fade losers are cut to ~40% size. Shuffle-null P<=0.004 (the confirm signal
    # genuinely selects losers, it is not "just trade smaller"). REGIME-DEPENDENT: in pure
    # chop ~90% confirm → all tax, no benefit; it pays when the tape carries continuation
    # risk. Per-pair via SYMBOL_CALIBRATION; ships OFF. USDJPY is the strong case (weak SELL
    # trend-fade leg), EURUSD marginal — arm USDJPY first. Reuses the tick-based favorable
    # displacement (same math as the retest arm) as the add trigger.
    "stage_entry_enabled": False,        # master per-pair switch (default OFF = today's behaviour)
    "stage_probe_frac": 0.40,            # size opened on the exhaustion tick
    "stage_add_frac": 0.60,              # size added on confirmation (probe+add = 1.0)
    "stage_confirm_pips": 2.0,           # favorable pull-away that triggers the ADD
    "stage_add_window_min": 30,          # minutes to confirm; after this the probe rides alone

    # ── Structure-trail exit — hold by DIRECTION, not by a fixed give-back ────────
    # DESIGNED 2026-08-14 (memory structure-trail). The problem: no fixed-distance
    # trail can separate a runner from a reverter (loose gives back on small winners,
    # tight chokes the runners — both tested, both lose). Fix: trail behind SWING
    # STRUCTURE. For a SELL, hold while price prints lower-highs; the stop rides just
    # above the last confirmed lower-high and ratchets down as new ones form. A wobble
    # that doesn't break the last lower-high is HELD; a break above it exits at the turn
    # (BUY mirrors: higher-lows, exit on break below the last higher-low). Noise-immune
    # (ignores sub-`reversal` wobbles) yet exits on a real reversal. When ON it REPLACES
    # the trail_dist_atr_mult ATR trail for that pair; breakeven + the hard SL/TP stay as
    # backstops. Per-pair via SYMBOL_CALIBRATION; ships OFF (offline-unvalidatable exit —
    # arm USDJPY-only, live A/B on daily results). Runs on BOTH accounts (each manages
    # its own trail from its own broker's M5 bars). See [[eurusd-mfe-giveback]] for why a
    # fixed trail can't win this.
    "structure_trail_enabled": False,    # master per-pair switch (default OFF = ATR trail unchanged)
    "structure_trail_reversal_atr": 0.4, # swing pivot confirms after price reverses this ×ATR
    "structure_trail_buffer_pips": 1.0,  # stop sits this many pips beyond the last swing pivot
    "structure_trail_lookback_m5": 80,   # M5 bars used to build the zigzag (~6.5h)
    # ── Reversal-confirmation PRE-ENTRY gate (SHADOW-ONLY, never acts) ─────────
    # The retest veto ENTERS EARLY then scratches; this asks the opposite question:
    # what if we WAIT and only enter once the reversal CONFIRMS? On each fired peak
    # signal the LEAD arms a watch anchored at the signal price and, over
    # revconf_window_sec, resolves: CONFIRM (price pulled +revconf_confirm_atr×ATR in
    # the fade/profit direction → would-enter at that LATER price) / INVALIDATE
    # (price ran revconf_invalidate_atr×ATR the WRONG way → would-skip, a false turn)
    # / TIMEOUT (neither by the window → would-skip). On CONFIRM it then simulates a
    # fresh hard-distance trade from the would-enter price to a WIN/LOSS/TIMEOUT
    # outcome, logging reprice cost + forward P&L to reports/reversal_confirm_shadow.jsonl.
    # WHY SHADOW-ONLY, NO ARM FLAG: the offline test of exactly this "delay-and-reprice"
    # LOST −90p and rescued ZERO losers (delaying forfeits the fast fades). This shadow
    # re-tests that on live fills before the idea is ever allowed to act. Never mirrored.
    "revconf_shadow": True,              # LEAD logs the would-wait counterfactual (no action)
    "revconf_confirm_atr": 0.25,         # favorable pull-away (×ATR) that CONFIRMS → would-enter
    "revconf_invalidate_atr": 0.25,      # adverse continuation (×ATR) that INVALIDATES → would-skip
    "revconf_window_sec": 900.0,         # seconds to resolve confirm/invalidate/timeout (15 min)
    "revconf_outcome_window_sec": 14400.0, # cap on the forward would-enter sim (4h) then mark-to-market
    # ── MTF market-structure shadow (Stage-1, LABEL-ONLY, never gates) ─────────
    # Stamps every fired fade with a structure verdict so we can prove whether
    # AGAINST-structure fades are the wrong-direction losers, before anything gates.
    # Builds an M15 zigzag (fractal pivots, min-amplitude filtered) → up/down/range,
    # reads the existing H1/H4 EMA trend, and labels the fade with_structure /
    # against_structure / range (a SELL is with-structure when the higher TF is DOWN).
    # Also tags the faded swing LH/HH (sell) or HL/LL (buy). No arm flag — Stage-1 is
    # a labeler; a per-pair veto is a later stage only if against_structure loses.
    "structure_shadow": True,            # LEAD stamps structure_shadow{} onto each signal
    "structure_swing_k": 2,              # fractal pivot half-window (bars each side)
    "structure_min_swing_atr": 0.5,      # min swing amplitude (×ATR) to count — kills micro-noise
    "structure_lookback_m15": 40,        # closed M15 bars the zigzag is built from (~10h)
    # When True (per-pair via SYMBOL_CALIBRATION), an "against_structure" verdict
    # VETOES the fade (skips it) instead of only logging — like the breakout veto, a
    # veto can only SKIP a trade, never add a loss. ARMED both pairs 2026-08-12 at
    # user direction to get real fills instead of shadow data. Watch trade count — it
    # removes fades that fight the higher-TF trend, which can be a large fraction.
    "structure_veto_enabled": False,
    # LOOSEN (2026-08-12): only ACT on the veto when a REAL H1 trend opposes the fade.
    # Live data showed the veto blocking ~85% of fades, ALL with h1=sideways — i.e. it
    # was firing on the noisier M15-zigzag fallback, which THRASHES in a ranging H1 (it
    # blocked BUYs then SELLs as the M15 flipped) and removed winning mean-reversion
    # fades (the passed trades won +0.9/+1.0p). With this True, an H1-sideways market
    # (the daemon's fade-edge zone) is left alone; the veto bites only when fighting a
    # committed H1 up/down trend. The shadow LABEL still uses the M15 fallback.
    "structure_veto_require_h1_trend": True,
    # ── Entry-selectivity shadow (SHADOW-ONLY, cut over-trading / commission drag) ─
    # The 1-month audit ([[commission-drag-analysis]]) showed the book is GROSS-profitable
    # but commission (−$7/trade × ~8/day) makes it net-negative — so FEWER, better fades
    # is the fix. Data (n=32) FALSIFIED the room-veto (high room = WORSE) but confirmed
    # "fading INTO recent momentum loses" (fading_into_impulse=True −$24.9/trade vs False
    # −$0.8; high displacement −$33 vs −$1.7). This shadow flags would_skip when a fade
    # fights the recent 300s move at >= selectivity_disp_threshold (LOWER than the
    # never-firing 0.55 impulse veto), and logs room/range for regime-tracking. NO arm
    # flag — validate that skipping these lifts net-after-commission first, then arm.
    "selectivity_shadow": True,
    "selectivity_disp_threshold": 0.30,  # displacement (net/path) that, when fading INTO it, would_skip
    # ── Regime-detection shadow (SHADOW-ONLY; the dynamic-market lever) ──────────
    # The wrong-direction trades are fundamentally a REGIME problem: the SAME fade is
    # right in a RANGE (level holds → hold it, give room) and wrong in a TREND (level
    # breaks → skip it). No static filter fixes both; regime is what tells them apart.
    # Can't pre-validate yet (recent sample is regime-homogeneous: ~all ranging, only
    # 2 trending trades), so this shadow LOGS regime per entry to collect the data.
    # Kaufman efficiency ratio (net move / total path over regime_lookback M15 closes):
    # >= regime_trend_er = trending, <= regime_range_er = ranging, else transitional.
    # would_skip = a fade fighting a STRONG trend (the "right spot, wrong direction" /
    # A-mode). NO arm flag — prove "skip in trend / hold in range" separates the early-
    # cut winners (B) from the wrong-direction losers (A) before wiring it.
    "regime_shadow": True,
    "regime_lookback": 20,               # M15 closes (~5h) for the efficiency ratio
    "regime_trend_er": 0.50,             # ER >= this = trending
    "regime_range_er": 0.30,             # ER <= this = ranging (else transitional)
    # REVP-confluence study: LEAD writes one exhaustion-telemetry row per closed
    # M15 bar (velocity_divergence ~ reversal pressure; efficiency ~ displacement)
    # to reports/revp_telemetry.jsonl, so the last untested pattern idea -- "pattern
    # AT a level WITH high reversal pressure" -- can be shadow-tested offline later.
    # Pure observation; never touches trading. Set False to stop the log.
    "revp_telemetry_log": True,

    # Impulse shadow: displacement_ratio threshold. Ratio >= this on a fade-
    # into-momentum entry logs "would_skip". Shadow-only (never blocks).
    "impulse_disp_threshold": 0.55,

    # MFE early-exit shadow: record where a dead-fade cutoff WOULD have exited.
    # Fires once when hold >= grace AND running MFE < floor AND running MAE >=
    # trigger. Shadow-only (never closes); close log stores saved_pips.
    "mfe_exit_shadow": True,
    "mfe_exit_grace_sec": 900.0,
    "mfe_exit_floor_pips": 5.0,
    "mfe_exit_mae_trigger_pips": 10.0,

    # Breakout discriminator shadow: flags a fade of a level that is BREAKING
    # (price beyond prior M15 structure by >= margin_atr×ATR AND a directional
    # push of >= push_atr×ATR over the last `window` bars) vs a real in-structure
    # reversal (passes). Logs "would_skip"/"allow" per entry.
    "breakout_shadow": True,
    "breakout_lookback": 20,
    "breakout_window": 3,
    "breakout_margin_atr": 0.25,
    "breakout_push_atr": 0.5,
    # When True (per-pair via SYMBOL_CALIBRATION), a "would_skip" breakout verdict
    # VETOES the entry instead of only logging it. Default OFF; armed USDJPY-only.
    "breakout_veto_enabled": False,

    # Exit-capture shadow: on every close, log what alternative exits WOULD have
    # realised — fixed TP caps (from MFE) and a tighter trailing leash (per-tick
    # sim). Pure observation; never touches the real SL. Motivated by EURUSD only
    # capturing ~42% of MFE (≈3p/trade given back to the 0.50×ATR trail).
    "exit_capture_shadow": True,
    "exit_shadow_trail_atr_mult": 0.35,          # tighter leash to test vs the live 0.50
    "exit_shadow_tp_pips": [3.0, 4.0, 5.0, 6.0], # fixed-TP caps to evaluate

    # Break-and-retest CONTINUATION shadow (mirror of the fade; never trades).
    # On M15 close: detect a breakout beyond prior structure, wait for a retest of
    # the broken level, confirm it holds, then track the forward outcome. Written to
    # reports/breakout_retest_shadow.jsonl for offline vs-shuffle-null validation.
    "breakout_retest_shadow": True,
    "br_lookback": 20,            # prior-structure M15 bars
    "br_window": 3,              # recent bars excluded from structure (the break zone)
    "br_margin_atr": 0.25,       # break must clear structure by this ×ATR
    "br_push_atr": 0.5,          # window trend push required to call it a break
    "br_retest_tol_atr": 0.2,    # retest zone half-width around the broken level
    "br_confirm_atr": 0.15,      # bar must close back past the level by this to confirm
    "br_invalidate_atr": 0.35,   # close back THROUGH the level by this = fakeout
    "br_retest_timeout_bars": 8, # M15 bars to wait for a retest before expiring
    "br_outcome_bars": 16,       # M15 bars to resolve the hypothetical entry
    "br_sl_atr": 1.0,            # hypothetical stop distance (×ATR)
    "br_tp_atr": 2.0,            # hypothetical target distance (×ATR)
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
        "hard_stop_pips": 20.0,      # hard SL = TP distance (hard_distance_mode). RESTORED 11->20
                                     # 2026-08-13 (user): keep the real/structural stop distance and
                                     # FLOAT the lot to 1.1% risk, instead of shrinking the stop to fit
                                     # a fixed 1.0 lot. Lead sizes via --risk-pct 1.1 (0.55 lot x 20p x
                                     # $10 = $110 = 1.1% of 10k); node via --max-loss-usd 1100 (5.5 lot).
        # No hard_trail_pips → the trail uses the adaptive trail_dist_atr_mult × ATR.
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 2.0,
        "min_stop_pips": 16.0,
        "max_stop_pips": 16.0,       # hard SL/TP ceiling when enforce_max_stop_pips=True
        "be_atr_mult": 0.40,            # breakeven arms at ~0.40xATR
        "trail_trigger_atr_mult": 0.50, # trail arms at ~0.50xATR
        "trail_dist_atr_mult": 0.35,    # TIGHTENED 0.50->0.35 2026-08-13 (LIVE, user "trail cuts small wins":
                                        # capture more of the ~4p peak in the current CHOP; scan found 0.35 beats
                                        # 0.50 net in ranging tape, fixed-TP even better). Judge on daily results;
                                        # WIDEN back toward 0.50+ when a trending week appears. Was ~0.50 TIGHT leash;
                                        # briefly 1.0 on 2026-08-12
                                        # (per wider-trail +47%) but a TICK-LEVEL replay of that day's
                                        # trades showed the wide trail captured LESS on EVERY winning
                                        # fade (net -$166) — it gives back in a REVERTING/choppy market.
                                        # wider-trail's +47% was on TRENDING data. Regime-dependent:
                                        # keep tight in chop; a regime-aware trail is the real fix.
        "direction_aware_sr": True,     # EURUSD reverts off structure — fade only OFF a
                                        # correct-side level, never INTO one. 249-trade study:
                                        # +$741 / PF 1.25->2.03 / drawdown halved.
        "structure_veto_enabled": True, # ARMED 2026-08-12 at user direction (both pairs). Skips
                                        # an against-structure fade (fights the higher-TF trend).
                                        # Bounded (only skips); thin evidence — watch trade count.
        "retest_confirm_enabled": False, # DISARMED 2026-08-13 (user: "early cuts are problem … disarm the veto"):
                                        # every loss on 2026-08-13 was a retest-veto scratch; trades left to
                                        # trail/hard-stop won. Shadow still logs (retest_confirm_shadow). Was
                                        # ARMED 2026-08-11 — Scratches a fade
                                        # that goes straight-against (>=retest_adverse_cap_pips
                                        # adverse BEFORE +retest_x_pips favorable) or stalls the
                                        # full window (timeout) — the "fades into continuation"
                                        # loser (72% of realised loss). EURUSD-validated: a
                                        # drawdown-halver at ~flat net (cuts losers, clips some
                                        # winners). CONFIRM = no-op (let the real reversal run).
        "retest_adverse_cap_pips": 3.0, # WIDENED 2026-08-12 from base 2.0 (EURUSD-modest). Live
                                        # data 2026-08-12 (a TRENDING day, not choppy) showed the
                                        # 2p cap scratching with-trend fades on normal pullbacks
                                        # right before they ran (+17 to +53p missed). Confirm stays
                                        # +2p (bias to hold). Bounded by the hard SL; EXPERIMENT on
                                        # n=1 day — revert if premature/protective mix doesn't improve.
        "stage_entry_enabled": False,   # STAGED ENTRY OFF for EURUSD — replay marginal here (+43->+50
                                        # sum, slightly negative in the late OOS half; fades confirm fast,
                                        # little left-tail to truncate). Arm only after USDJPY proves out.
        "structure_trail_enabled": False, # STRUCTURE-TRAIL OFF for EURUSD — arm USDJPY first, judge on
                                        # daily results, then consider EURUSD (its moves are smaller;
                                        # reversal auto-scales with its ~6p ATR).
    },
    "USDJPY": {
        "magic_number": 123458,      # distinct from EURUSD
        "pip_size": 0.01,
        # USD-BASE pair: $/pip/lot is price-dependent (~contract*pip/price ≈ $6–7),
        # not a constant. None → compute dynamically at order time from live price.
        "pip_value_per_lot": None,
        "risk_pct": 0.01,
        "max_lot": 2.0,              # hard safety ceiling; 1% risk sizes to ~1.0 lot
        "hard_stop_pips": 30.0,      # hard SL = TP distance (hard_distance_mode). RESTORED 17.5->30
                                     # 2026-08-13 (user): keep the real stop distance, FLOAT the lot to
                                     # 1.1% risk. Lead via --risk-pct 1.1 (0.58 lot x 30p x ~$6.29 =
                                     # $110 = 1.1% of 10k); node via --max-loss-usd 1100 (5.83 lot).
        # No hard_trail_pips → the trail uses the adaptive trail_dist_atr_mult × ATR.
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 2.0,
        "min_stop_pips": 16.0,
        "max_stop_pips": 10.0,       # hard SL/TP ceiling when enforce_max_stop_pips=True
        "be_atr_mult": 0.40,            # breakeven arms at ~0.40xATR
        "trail_trigger_atr_mult": 0.50, # trail arms at ~0.50xATR
        "trail_dist_atr_mult": 0.35,    # TIGHTENED 0.50->0.35 2026-08-13 (LIVE, user "trail cuts small wins":
                                        # capture more of the ~4p peak in the current CHOP; scan found 0.35 beats
                                        # 0.50 net in ranging tape, fixed-TP even better). Judge on daily results;
                                        # WIDEN back toward 0.50+ when a trending week appears. Was ~0.50 TIGHT leash;
                                        # briefly 1.0 on 2026-08-12
                                        # (per wider-trail +47%) but a TICK-LEVEL replay of that day's
                                        # trades showed the wide trail captured LESS on EVERY winning
                                        # fade (net -$166) — it gives back in a REVERTING/choppy market.
                                        # wider-trail's +47% was on TRENDING data. Regime-dependent:
                                        # keep tight in chop; a regime-aware trail is the real fix.
        "direction_aware_sr": False,    # USDJPY BREAKS its levels — wrong-side fades are
                                        # continuation WINNERS (PF 2.54). Leave OFF.
        "breakout_veto_enabled": True,  # RE-ARMED 2026-08-11 at user direction, accepting the
                                        # thin-evidence risk (in-sample n=2). Bounded downside:
                                        # this ONLY skips a fade into a breaking level — it can
                                        # never add a loss, worst case is a missed win. Skips are
                                        # logged (signal_skipped, "breakout veto: …") so the block
                                        # rate + what it turned away stay auditable at checkpoint.
        # TIGHTENED 2026-08-11 (user: "tighten the veto's push threshold"). The veto
        # fires only on fresh_extreme AND strong_push. Both logged USDJPY sells had
        # fresh_extreme=False, so PUSH ALONE WAS INERT — the +16.5p WINNER even had the
        # biggest push (+19.5p). The binding knob is the margin (fresh_extreme) gate, so
        # tighten it too: margin 0.25->0.10xATR (~1.9p) lets a fade entering just BEYOND a
        # broken level trip fresh_extreme, while the winners (ext -11.6/-16.3p, deep INSIDE
        # structure) stay allowed with a wide buffer. Push 0.5->0.35xATR (~6.6p) is the
        # confirmation, now meaningful once margin can fire. Still per-pair USDJPY-only;
        # n=2 so this is a watch-item, not a proven number.
        "breakout_margin_atr": 0.10,
        "breakout_push_atr": 0.35,
        "retest_confirm_enabled": False, # DISARMED 2026-08-13 (user: early cuts / disarm the veto). Shadow still
                                        # logs. Was ARMED 2026-08-11 — Same straight-against
                                        # scratch as EURUSD, and it is the RIGHT lever for today's
                                        # USDJPY loser (sold 16p INSIDE the range → the breakout
                                        # veto structurally can't catch that; the retest veto CAN,
                                        # and it flagged it "veto" live). THINNER EVIDENCE than
                                        # EURUSD (validated EURUSD-only) — watch confirm-vs-veto
                                        # accuracy at the checkpoint; it can clip winning runners.
        "structure_veto_enabled": True, # ARMED 2026-08-12 (both pairs). Skips an against-structure
                                        # fade (fights the higher-TF trend). Bounded (only skips);
                                        # thin evidence — watch trade count, disarm if over-restricts.
        "retest_adverse_cap_pips": 5.0, # WIDENED 2026-08-12 from base 2.0 (USDJPY-wider — 2p is a
                                        # tiny fraction of its ~19p ATR). 3 of 4 premature scratches
                                        # on 2026-08-12 were USDJPY sells cut on a pullback then ran
                                        # +40/+53/+41p. Gives room for the pullback; hard SL (17.5p)
                                        # still backstops. Confirm stays +2p. EXPERIMENT (n=1 day).
        "stage_entry_enabled": False,   # STAGED ENTRY — the replay-validated arm point (memory
                                        # confirmation-by-degree-replay). Set True to enter a 40% probe
                                        # on the exhaustion tick + add 60% on the first +2p confirm; the
                                        # 7 straight-against SELL losers in the sample rode at 40% size
                                        # (-158p -> -63p), total +105 -> +152p, drop-best-robust. Ships
                                        # OFF; enable + restart WHILE FLAT to arm. probe+add stay 1.0.
        "structure_trail_enabled": False, # STRUCTURE-TRAIL — the arm point (memory structure-trail). Set
                                        # True to REPLACE the ATR trail with a swing-structure trail: hold
                                        # while lower-highs form, exit on a break above the last lower-high.
                                        # Designed to stop giving back on small winners AND stop choking the
                                        # +30 runners (both failure modes of a fixed trail). reversal auto-
                                        # scales with ATR (~5-6p here). Ships OFF; enable + restart to arm
                                        # (exit-only, so restart-while-flat is safer but not required).
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
    # Hard SL/TP distance in pips (used only when hard_distance_mode=True).
    cfg["realtime_hard_stop_pips"] = spec.get(
        "hard_stop_pips", base.get("realtime_hard_stop_pips")
    )
    # Hard TRAIL distance in pips (tighter than the stop so the trail locks profit
    # before the TP). None → trail falls back to the stop distance.
    cfg["realtime_hard_trail_pips"] = spec.get(
        "hard_trail_pips", base.get("realtime_hard_trail_pips")
    )
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
    # Per-pair direction-aware S/R flag (whitelist mapping — without this line the
    # SYMBOL_CALIBRATION key never reaches the daemon's self.config). Default OFF.
    cfg["direction_aware_sr"] = spec.get(
        "direction_aware_sr", base.get("direction_aware_sr", False)
    )
    # Per-pair retest-confirmation veto (whitelist mapping — without these lines the
    # SYMBOL_CALIBRATION keys never reach self.config). Defaults keep it shadow/OFF;
    # arm EURUSD-only once the shadow log confirms it cuts losers, not winners.
    for _k, _d in (("retest_confirm_enabled", False), ("retest_confirm_shadow", True),
                   ("retest_x_pips", 2.0), ("retest_adverse_cap_pips", 2.0),
                   ("retest_window_min", 30)):
        cfg[_k] = spec.get(_k, base.get(_k, _d))
    # Per-pair staged (confirmation-by-degree) entry (whitelist mapping — without these
    # lines the SYMBOL_CALIBRATION keys never reach self.config). Ships OFF; arm USDJPY
    # first (replay-validated loss-truncator). probe+add MUST sum to 1.0 (risk-constant).
    for _k, _d in (("stage_entry_enabled", False), ("stage_probe_frac", 0.40),
                   ("stage_add_frac", 0.60), ("stage_confirm_pips", 2.0),
                   ("stage_add_window_min", 30)):
        cfg[_k] = spec.get(_k, base.get(_k, _d))
    # Per-pair structure-trail exit (whitelist mapping — without these lines the
    # SYMBOL_CALIBRATION keys never reach self.config). Ships OFF; when armed it REPLACES
    # the ATR trail for that pair. Arm USDJPY-first (live A/B). See memory structure-trail.
    for _k, _d in (("structure_trail_enabled", False), ("structure_trail_reversal_atr", 0.4),
                   ("structure_trail_buffer_pips", 1.0), ("structure_trail_lookback_m5", 80)):
        cfg[_k] = spec.get(_k, base.get(_k, _d))
    # Per-pair breakout discriminator (whitelist mapping — without these lines the
    # SYMBOL_CALIBRATION keys never reach self.config). Shadow logs for all pairs;
    # the veto acts only where breakout_veto_enabled is set (USDJPY-only).
    for _k, _d in (("breakout_veto_enabled", False), ("breakout_shadow", True),
                   ("breakout_lookback", 20), ("breakout_window", 3),
                   ("breakout_margin_atr", 0.25), ("breakout_push_atr", 0.5)):
        cfg[_k] = spec.get(_k, base.get(_k, _d))
    # Per-pair market-structure shadow/veto (whitelist mapping — without these the
    # SYMBOL_CALIBRATION keys never reach self.config). Label logs for all pairs; the
    # veto acts only where structure_veto_enabled is set.
    for _k, _d in (("structure_veto_enabled", False), ("structure_shadow", True),
                   ("structure_swing_k", 2), ("structure_min_swing_atr", 0.5),
                   ("structure_lookback_m15", 40), ("structure_veto_require_h1_trend", True)):
        cfg[_k] = spec.get(_k, base.get(_k, _d))
    return cfg
