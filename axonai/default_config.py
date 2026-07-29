import os

_AXONAI_HOME = os.path.join(os.path.expanduser("~"), ".axonai")

# Single source of truth for env-var → config-key overrides. To expose
# a new config key for environment-based override, add a row here — no
# entry-point script changes required. Coercion is driven by the type
# of the existing default, so users can keep writing plain strings in
# their .env file.
_ENV_OVERRIDES = {
    "AXONAI_MT5_TERMINAL_PATH":    "mt5_terminal_path",
    "AXONAI_MT5_SYMBOL_SUFFIX":    "mt5_symbol_suffix",
    "AXONAI_REALTIME_MAGIC_NUMBER": "realtime_magic_number",
    "AXONAI_REALTIME_DEFAULT_LOT_SIZE": "realtime_default_lot_size",
    "AXONAI_REALTIME_DEVIATION": "realtime_deviation",
    "AXONAI_REALTIME_MIN_CONFLUENCE_CONDITIONS": "realtime_min_confluence_conditions",
    "AXONAI_REALTIME_DRY_RUN": "realtime_dry_run",
    "AXONAI_REALTIME_MIN_PRICE_DISTANCE_TO_TRAIL": "realtime_min_price_distance_to_trail",
    "AXONAI_REALTIME_MAX_TRAIL_DISTANCE": "realtime_max_trail_distance",
    "AXONAI_REALTIME_BASE_TRAIL_BUFFER": "realtime_base_trail_buffer",
    "AXONAI_REALTIME_MIN_TRAIL_FLOOR_PIPS": "realtime_min_trail_floor_pips",
    "AXONAI_REALTIME_MAX_RISK_USD": "realtime_max_risk_usd",
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
    # MetaTrader 5 integration
    "mt5_terminal_path": "C:\\Program Files\\MetaTrader 5 EXNESS\\terminal64.exe",  # Data feed (TickEngine)
    "mt5_trade_terminal_path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe",   # Order execution (TradeExecutor)
    "mt5_symbol_suffix": "",       # Broker symbol suffix: EURUSD
    "mt5_timeframes": ["M15", "H1", "H4", "D1"],  # Multi-TF analysis order
    # ── Real-time daemon settings ────────────────────────────────────────
    "realtime_enabled": True,
    "tick_poll_interval_ms": 100,
    "realtime_cooldown_seconds": 300,
    "realtime_min_event_priority": "MEDIUM",
    "realtime_tick_buffer_size": 10_000,
    "realtime_candle_history": 500,
    "realtime_suppress_asian": False,    # False = Allow Asian trading (with 0.25 penalty)
    "realtime_level_reset_atr_multiple": 2.0,
    "realtime_log_events": True,
    "realtime_magic_number": 123456,
    "realtime_default_lot_size": 0.01,
    # Live sizing: when False, every live order uses realtime_default_lot_size.
    # When True, size is risk-based off account equity (realtime_risk_pct),
    # capped at realtime_max_lot.
    # Pure 1% risk hard-lock: every trade risks exactly realtime_risk_pct of
    # equity; lot floats with equity & stop distance (see trade_executor sizing).
    "realtime_dynamic_sizing": True,
    "realtime_risk_pct": 0.01,           # HARD 1% risk per trade
    "realtime_max_risk_usd": 100.0,      # Strict maximum risk limit per trade in account currency
    "realtime_max_lot": 2.00,            # backstop ceiling vs pip-miscalc blow-ups (was 5.0 — too high)
    "realtime_max_lot_gold": 1.00,       # gold-specific ceiling (XAUUSD pip_value ≈ $1/lot → 5.0 = $500/pip)
    "realtime_deviation": 20,
    "realtime_min_confluence_conditions": 1,
    "realtime_dry_run": False,
    # ── Portfolio-level risk caps (account-wide, enforced pre-trade) ─────────
    "max_concurrent_positions": 5,       # max simultaneous open positions across ALL symbols (0 = disabled)
    "max_daily_loss_usd": 500.0,         # halt new entries once the day's realized loss hits this (0 = disabled)
    "max_same_direction_positions": 0,   # correlation cap: max same-direction concurrent (0 = disabled)
    # Currency-exposure cap (2026-07-30): net signed units per currency across all
    # open positions. All 4 traded pairs share a USD leg, so USDJPY BUY and
    # AUDUSD SELL are BOTH +USD -- one doubled 'long USD' bet that stopped out
    # together on 07-30 (-200 in 23min). 1 = allow offsetting pairs but block
    # stacking the same currency direction; 0 = disabled. (count-based; assumes
    # equal-size trades.)
    "max_currency_exposure": 1,
    # ── Reversal-edge entry filter (data-derived; additive veto, only blocks) ──
    # From winners-vs-losers analysis: RANGE_CHOP loses; reversals fire at a
    # per-pair velocity climax. Gold has NO clean velocity edge, so it uses a
    # volatility floor instead (kept in the system, own thresholds).
    "reversal_edge_gate_enabled": True,
    # Per-pair regime blocks (snapshot study 2026-07-17): EURUSD 30/39 and
    # AUDUSD 39/42 of real reversals happen IN RANGE_CHOP — the global chop
    # block was closing exactly their opportunity window. GBPUSD turns live in
    # TREND_CONTINUATION (45/60), so its chop block stays. XAUUSD is ~93% chop
    # by tick, so a chop block just disables gold; its guard is the
    # counter-trend hard block in the confluence gate instead.
    "reversal_block_regimes": {
        "default": ["RANGE_CHOP"],
        "EURUSD": [],
        "AUDUSD": [],
        "XAUUSD": [],
    },
    # Per-pair conviction floor INSIDE an allowed chop regime. AUD reversals
    # live in RANGE_CHOP (see reversal_block_regimes), so the pair is not chop-
    # blocked — but 2026-07-30 live showed the losers there were NEUTRAL /
    # reversal_pressure=0 fades, while the one exhaustion+revP=1.0 setup did not
    # run to SL. So instead of blocking chop (which would kill ~93% of AUD's
    # reversal window), require real conviction when trading in these regimes:
    # pass only if displacement is EXHAUSTION or reversal_pressure >= floor.
    # Empty/unset for a pair = no extra gate (other pairs stay byte-identical).
    "reversal_chop_conviction": {
        "AUDUSD": {"regimes": ["RANGE_CHOP"], "min_reversal_pressure": 0.6},
    },
    "reversal_require_location": False,   # enable after location logging is validated live
    "reversal_location_max_pips": {"default": 8.0, "XAUUSD": 60.0},
    # Floors sit between p25 and p50 of LIVE TRIGGERED distributions (measured
    # 2026-07-16 over 1448/1787/655/4/8183 triggers). The previous hand-set
    # floors were above p50+ on every FX pair (EUR vol 2.0 vs live p50 0.81),
    # which silently blocked 100% of FX entries. Daily calibration overrides
    # these per pair when enough reversal events exist.
    "reversal_pair_floors": {
        "EURUSD": {"vel_pct": 35, "vol_pips": 0.75, "tick_eff": 0.25},
        "GBPUSD": {"vel_pct": 35, "vol_pips": 1.25, "tick_eff": 0.25},
        "USDJPY": {"vel_pct": 40, "vol_pips": 0.70, "tick_eff": 0.30},
        "AUDUSD": {"vel_pct": 35, "vol_pips": 0.50, "tick_eff": 0.25},
        "XAUUSD": {"vel_pct": 0,  "vol_pips": 175,  "tick_eff": 0.15},  # gold: vol-based, no vel floor; tracks calibrated 174.56
    },
    # Exit/hold tuning (journal study 2026-07-17): trail armed at +5p with a 4p
    # floor parked SL at +1-2p, so noise pullbacks clipped trades at +2-4p while
    # winners run +17-20p. Arm later and trail wider so profits can breathe;
    # both scale by pair_move_scale automatically (gold x10).
    "realtime_trail_activation_pips": 8.0,   # was 5.0 default in code
    "realtime_min_trail_floor_pips": 6.0,    # was 4.0 default in code
    # Structure-fade SHADOW detector (logs level-fade signals, never trades).
    # Spec mirrors the offline validation (60-76% FX directional accuracy).
    # Promote to a real entry path only after live shadow signals confirm.
    "structure_fade_shadow": True,
    "fade_revp_min": 0.8,        # reversal_pressure confirmation threshold
    "fade_vel_pct_max": 55.0,    # above this = spike; anomaly path's territory
    "fade_dist_pips": 6.0,       # max distance from the level being faded
    "fade_cooldown_sec": 300.0,  # one signal per episode, not per tick
    # Calibrated Gold Entry Thresholds
    # vel cap 30 contradicted gold's own calibration: median vel_pct at 103
    # measured pre-reversal events = 41.6 (the cap vetoed ~half of genuine
    # setups, 992 'velocity too high' skips). ~p75 of observed events = 55.
    "entry_max_velocity_pct_gold": 55.0,
    "entry_min_decay_ratio_gold": 0.25,
    "entry_max_tick_efficiency_gold": 0.30,
    # Climax credit (reversal_model): FX uses the absolute cutoff; gold ranks
    # efficiency against its own distribution so the credit isn't handed out
    # every tick. eff<0.2 on FX == ~bottom 28% of FX efficiency historically.
    "entry_climax_eff_abs": 0.2,
    "entry_climax_eff_percentile_gold": 28.0,
    "entry_min_stall_duration": 15.0,
    "paper_trade": True,           # True = simulate fills internally, never call mt5.order_send (safe for tests / funded accounts)
    "peak_detector_rule_c_enabled": False,
    "trade_risk_pct": 0.01,
    "realtime_use_pinpoint_price": False,
    "realtime_correct_rule_a_direction": True,   # corrected Rule-A reversal direction (buy-spike→SELL, sell-spike→BUY)
    "realtime_max_spread_frac": 0.5,             # reject entry if spread > this fraction of the stop distance
    "realtime_min_signal_quality": 0.50,         # confluence-score floor for entries (reduced from 0.60 for more trades)
    "realtime_cooldown_bypass_better_peak": False,
    "realtime_min_profit_for_velocity_exit": 3.0,
    "realtime_velocity_decay_profit_factor": 0.75,
    "realtime_velocity_decay_threshold_aligned": 0.20,
    "realtime_velocity_decay_threshold_unaligned": 0.40,
    "decay_ticks_threshold": 10,
    # ── Trailing Stop settings ──────────────────────────────────────────
    # Retest = price tested the SL zone AND bounced back. Was proximity-only, which
    # armed the trail while price was still falling toward the stop and ratcheted it
    # tighter the closer price got. True requires the bounce (retest_bounce_pips) off
    # the adverse extreme; False restores the legacy proximity-only behaviour.
    "trail_retest_require_bounce": True,
    # Breakeven / peak-lock ratchet, default OFF. Manages the profit band BELOW the
    # momentum trail's activation (realtime_trail_activation_pips=5.0), which the
    # trail never touches, so a winner peaking at e.g. +4p no longer round-trips to
    # the initial SL (~-8p). One-way: only tightens toward profit. be_arm_pips of
    # PEAK profit moves the SL to entry+be_offset_pips (covers spread); past
    # be_lock_start_pips it locks be_lock_frac of the peak. Values sit inside the
    # ~2.4-5p dead band on purpose. Kept off until a restarted session shows where
    # MFE lands under exit_min_hold_seconds — tuning to the pre-restart distribution
    # is the mistake to avoid. All pip values scale by pair_move_scale.
    "be_lock_enabled": False,
    "be_arm_pips": 1.5,
    "be_offset_pips": 0.2,
    "be_lock_start_pips": 2.0,
    "be_lock_frac": 0.5,
    "realtime_min_price_distance_to_trail": 2.0,
    "realtime_max_trail_distance": 15.0,
    "realtime_base_trail_buffer": 7.5,
    # NOTE: realtime_min_trail_floor_pips is set above (6.0). It used to be
    # re-declared here as 4.0, and because a later key wins in a dict literal
    # that silently reverted the 2026-07-17 journal-study widening. Do not
    # re-add it here.

    # ── EOD (End-of-Day) position close ─────────────────────────────────────
    # Force-close all open positions when the trading day winds down, matching
    # the backtester's session-transition close. Fires once when the live
    # session rolls from an active session into a wind-down session.
    "eod_close_enabled": True,
    "eod_close_active_sessions": ["london", "overlap", "newyork"],   # day sessions
    "eod_close_trigger_sessions": ["rollover", "asian"],             # roll → close

    # ── Dynamic News Guard ──────────────────────────────────────────────────
    # Block new entries around high-impact economic news for either currency in
    # the traded pair. Calendar fetched from ForexFactory's free weekly JSON and
    # cached to <data_cache_dir>/economic_calendar.json (offline fallback).
    "news_guard_enabled": True,
    "news_guard_block_impacts": ["High"],   # impact levels that trigger a blackout
    "news_guard_pre_minutes": 30,           # blackout starts N min before event
    "news_guard_post_minutes": 30,          # blackout ends N min after event
    "news_guard_calendar_url": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "news_guard_refresh_hours": 6,          # re-fetch calendar at most this often

    "sl_atr_multiple": 1.0,
    "tp_atr_multiple": 1.5,

    # ── Placeholder TP (ExitEngine drives actual exits) ──────────────────────
    "placeholder_tp_sl_multiple": 3.0,

    # ── Trade Lifecycle Phase Transitions ──────────────────────────────────
    "trade_phase_min_duration_ticks": 30,              # debounce: min ticks before phase transition
    "at_structure_atr_threshold": 0.5,                # distance to S/R in ATR units before "at_structure"
    "expansion_phase_min_displacement": 2.0,          # pips of net move to confirm EXPANSION
    "exhaustion_detection_velocity_max": 30,          # percentile threshold (velocity > this → high activity)
    "exhaustion_detection_displacement_max": 0.3,     # displacement ratio (< this → trapped)

    # ── Exit Engine Priority Urgency Multipliers ────────────────────────────
    "thesis_failure_urgency": 1.0,                    # highest
    "adverse_impulse_urgency": 0.9,
    "adverse_impulse_min_ticks": 30,                  # min-hold before adverse-impulse CLOSE_NOW can fire
    "exhaustion_urgency": 0.7,
    "trailing_stop_urgency": 0.3,                     # lowest (legacy fallback)
    # Exhaustion CLOSE_NOW disabled 2026-07-20: profit is booked only via the velocity
    # trail + defined SL/TP, to test whether trades ride further without the exhaustion
    # cut. Flip back to True to restore the exhaustion exit.
    "exit_engine_enable_exhaustion": False,
    # adverse_impulse disabled 2026-07-23. Measured across 109 real FX trades it
    # is the only net-negative gate: n=33, net -69.8p, expectancy -2.12p, while
    # thesis_failure is +43.5p and exhaustion +4.9p.
    "exit_engine_enable_adverse_impulse": False,
    # Minimum hold before ANY soft gate may close a position. SL/TP still apply
    # throughout. Measured 2026-07-23: the market's median favourable excursion
    # is 2.80p at a 15m hold, 5.50p at 60m, 11.32p at 240m, while round-trip
    # cost is a flat ~0.8-1.2p — so gross opportunity scales with holding time
    # but cost does not. Our trades were closing in 2-4 minutes with a median
    # MFE of 2.10p, below even the 15-minute ceiling. 0.0 = previous behaviour.
    "exit_min_hold_seconds": 3600.0,
    # min-hold is gate-SPECIFIC: True suppresses only the noise cutters
    # (adverse_impulse, exhaustion) during the hold window while thesis_failure
    # and the RETEST_TRAP protective hold keep running. thesis_failure is net
    # +43.5p over 109 trades, so gagging it for an hour would throw away the one
    # soft gate that works. False = legacy behaviour (suppress everything until
    # the window elapses).
    "exit_min_hold_exempt_thesis": True,

    # ── Entry: retest directional-approach gate ─────────────────────────────
    # True = a retest trigger requires price to pull away from the anomaly level and then
    # come BACK toward it before firing (fixes SELL-at-bottom / BUY-at-top mistimed entries).
    # False = legacy symmetric-zone behaviour (fire on first momentum stall in the zone).
    "entry_retest_require_approach": True,
    # Optimistic velocity-decay trigger, disabled 2026-07-23. It transitioned
    # ARMING -> TRIGGERED without reading `dist` at all (no breakaway, no
    # location, no retest), and was responsible for the bulk of the 98-99% of
    # trigger events that skipped RETEST_WAIT entirely. True restores it.
    "entry_enable_optimistic_decay_trigger": False,
    # RETEST_WAIT timeout. Was hardcoded at 300.0. With both ARMING bypasses closed
    # (optimistic decay + strong_reversal), RETEST_WAIT is the ONLY route to an entry,
    # so this now sets the trade rate outright: a retest that does not complete inside
    # it is discarded. Raise it if "timeout" dominates the outcome tally that
    # EntryStateMachine logs on every retest timeout.
    "entry_retest_timeout_sec": 300.0,
    # Pips beyond the OBSERVED EXTREME (not the level) that price may push during a
    # retest before the setup is called a breakout. Previously this check had no
    # buffer at all and measured against the level, so a retest wick a fraction of a
    # pip through the level killed a valid setup. Defaults to the ARMING buffer.
    "entry_retest_break_buffer_pips": 2.0,
    # Velocity confluence counted `is_decaying` and `decay < 0.5` as two separate
    # confirmations. They are one event: measured over live snapshots they fire at
    # byte-identical frequency on every symbol (EUR 10.3%, GBP 20.1%, AUD 7.6%,
    # JPY 11.9%). Summing them let a single velocity reading buy the full 0.25
    # velocity weight. True takes the stronger of the two; False restores the sum.
    # Typical effect is about -0.05 on the confluence score against a 0.65 gate.
    "entry_dedupe_correlated_velocity": True,
    # reversal_pressure high-decay term. Added up to +0.5 pressure when decay_ratio
    # > 0.6 on the claim "reversals show 0.75-0.91 decay". Measured over 88k snapshot
    # ticks: decay_ratio has no forward relationship to reversals (all buckets within
    # +/-0.06p, noise vs a 0.6p spread), and all 69 real FX entries fired at decay
    # < 0.5 so the term never applied at entry. Default off. True restores it.
    "reversal_pressure_high_decay_term": False,
    # Room-to-next-level veto (pips), measured in the PROFIT direction (BUY -> room
    # to the next resistance above, SELL -> room to the next support below). Reject
    # an entry with less room ahead than this. Trade log: room<1p entries netted
    # -0.67p/trade at 33% win, room>=1p netted ~+0.6p at 50%, and 52% of fills were
    # in the losing bucket; vetoing <1p flipped the FX sample -4.2p -> +19.9p (69
    # trades, in-sample). 10.0 is the no-level-that-side sentinel (open space) and
    # never vetoes. 0.0 = disabled; recommended 1.0 by the data. Off by default so
    # the direction-aware veto is confirmed on a restarted session before it gates
    # live money.
    # Enabled 2026-07-30: FX realized-trade cohorts (n=121, gold excluded) show
    # room>=1p netted +0.29p/46% win vs room<1p -0.72p/32% win -- the cleanest
    # single W/L split in the book. 1.0 vetoes the losing bucket, keeps the winner.
    "entry_min_room_pips": 1.0,

    # Regime-avoidance veto for reversal entries. Signal-level feature importance
    # over 809k old-engine snapshot ticks: fading in TREND_EXPANSION netted -0.62p
    # and COMPRESSION -0.18p forward-30m reversal return, while every continuous
    # feature scored ~0 (|rho|<=0.03) -- the discriminating structure is regime/
    # state, not the continuous indicators. Avoidance filter, not an entry signal.
    # Empty = disabled. Candidate values ["TREND_EXPANSION", "COMPRESSION"], kept
    # off until a restarted session confirms these regimes stay negative under the
    # corrected entry engine (old-engine snapshots computed the evidence).
    # Enabled 2026-07-30: FX realized-trade cohorts (n=121, gold excluded) show
    # fading in TREND_CONTINUATION is the worst regime (-0.39p/36% win, n=67) --
    # these are fades INTO a live trend (tonight's USDJPY -101.67 SL was one).
    # RANGE_CHOP fades were the best (+0.16p/53%), so only the trend regime is
    # vetoed, not chop. Regime taxonomy is the live daemon's, not the old-engine
    # TREND_EXPANSION study above.
    "entry_avoid_regimes": ["TREND_CONTINUATION"],

    # ── HTF Coherence Dampening ────────────────────────────────────────────
    "htf_opposing_sensitivity_multiplier": 1.5,       # more aggressive exits when HTF opposes
    "htf_aligned_sensitivity_multiplier": 0.7,        # less aggressive exits when HTF aligns

    # ── Dashboard & Timing ─────────────────────────────────────────────────
    "dashboard_broadcast_interval_ms": 125.0,         # throttle broadcasts to 8 Hz max
    "dashboard_mt5_poll_interval_seconds": 1.0,       # slow poll thread interval
    "latency_instrumentation_enabled": True,          # enable timing logs for diagnostics

    "loss_cooldown_minutes": 45,
    "cooldown_seconds": 300,
    "stagnation_limit": 2700,
    "drawdown_limit_trending": 2400,
    "drawdown_limit_ranging": 2700,
    "indicator_rsi_length": 14,
    "indicator_ema_fast": 12,
    "indicator_ema_slow": 26,
})


# ── Per-symbol confluence-score floor ───────────────────────────────────────
# Single source of truth shared by the live daemon and the intraday backtester
# so live selectivity == backtest selectivity. Matched by substring (upper-cased,
# suffix-stripped), so "GBPUSD", "GBPUSD=X" and "GBPUSD.r" all resolve.
SIGNAL_QUALITY_BY_SYMBOL = {
    "GBPUSD": 0.55,
    "USDJPY": 0.60,
    "AUDUSD": 0.45,
    "EURUSD": 0.50,
    "XAUUSD": 0.65,  # worst-performing pair carries the strictest floor (was falling to 0.50 default)
}
SIGNAL_QUALITY_DEFAULT = 0.50


def signal_quality_for(symbol: str) -> float:
    """Return the per-symbol confluence-score floor for `symbol`.

    Falls back to SIGNAL_QUALITY_DEFAULT for any symbol not in the map.
    """
    s = (symbol or "").upper().replace("=X", "").replace("/", "")
    for key, val in SIGNAL_QUALITY_BY_SYMBOL.items():
        if key in s:
            return val
    return SIGNAL_QUALITY_DEFAULT


# ── Strategy version stamp ─────────────────────────────────────────────────
# Every trade carries the version of the entry logic + active experiment flags
# that produced it, so later analysis never has to infer which configuration was
# live. Bump STRATEGY_VERSION_BASE when the entry CODE changes; the flag suffix
# is derived automatically, so flipping a phase flag (room / regime / be_lock)
# moves trades onto a new, distinguishable version with no manual edit.
STRATEGY_VERSION_BASE = "entry_v5.1"


def strategy_version(config: dict | None = None) -> str:
    """Version fingerprint of the live entry configuration, for trade tagging."""
    c = config or {}
    room = 1 if float(c.get("entry_min_room_pips", 0.0) or 0.0) > 0 else 0
    regime = 1 if (c.get("entry_avoid_regimes") or []) else 0
    belock = 1 if c.get("be_lock_enabled", False) else 0
    return f"{STRATEGY_VERSION_BASE}_room{room}_regime{regime}_belock{belock}"
