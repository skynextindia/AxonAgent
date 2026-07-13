"""Advanced Microstructure Reversal Model.

This is the top-level orchestrator that replaces `TickBehaviorAnalyzer` and
`PeakDetector`. It instantiates the 10 new modules and acts as the
primary entry point for `daemon.py` and `backtester.py`.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from axonai.realtime.live_state import PriceLevel
from axonai.realtime.event_types import LiveCandle
from axonai.realtime.candle_setup_tracker import CandleSetupTracker

# Tier 1: Data
from axonai.realtime.velocity_normalizer import VelocityNormalizer, NormalizedVelocity
from axonai.realtime.displacement_engine import DisplacementEngine, DisplacementState

# Tier 2: Analysis
from axonai.realtime.regime_engine import RegimeEngine, RegimeState
from axonai.realtime.mtf_context import MTFContext, MTFState
from axonai.realtime.liquidity_engine import LiquidityEngine, LiquidityState

# Tier 3: Execution
from axonai.realtime.entry_state_machine import EntryStateMachine, EntryDecision
from axonai.realtime.trade_health_monitor import TradeHealthMonitor, TradeHealth
from axonai.realtime.adaptive_exit import AdaptiveExitManager, ExitDecision
from axonai.realtime.trade_phase import TradePhaseTracker
from axonai.realtime.exit_stats import ExitStats

# NEW: Tier 3 Lifecycle Engines
from axonai.realtime.location_engine import LocationEngine, LocationContext
from axonai.realtime.trade_state_engine import TradeStateEngine, TradeState
from axonai.realtime.exit_engine import ExitEngine

logger = logging.getLogger(__name__)


@dataclass
class EngineSnapshot:
    """A complete snapshot of all engine states at a given tick."""
    timestamp: float
    price: float
    velocity: NormalizedVelocity
    displacement: DisplacementState
    regime: RegimeState
    mtf: MTFState
    liquidity: LiquidityState
    entry_decision: EntryDecision
    trade_health: TradeHealth
    exit_decision: ExitDecision
    # NEW: Include lifecycle fields
    trade_state: Optional[TradeState] = None
    location_context: Optional[LocationContext] = None
    atr: float = 0.0


def _unified_confluence_score(
    direction: Optional[str],
    price: float,
    pip: float,
    h1_atr: float,
    mtf: MTFState,
    liq: LiquidityState,
    vel: NormalizedVelocity,
    disp: DisplacementState,
    price_levels: Optional[List[PriceLevel]],
    candle_setup_score: float = 0.0,
    config: Optional[dict] = None,
):
    """Unified confluence gate replacing _reversal_confluence_grade.

    Weights:
        0.30  Candle Setup Score    (CandleSetupTracker — M15 confirmed pattern)
        0.25  Tick Velocity Exhaust (VelocityEngine — climax + decay at extreme)
        0.25  S/R Level Proximity  (distance to nearest active level)
        0.20  H4/H1 Trend Align    (MTF EMA bias direction)

    Threshold: >= 0.65 to allow entry.  Hard-rejects kept for structural breaks
    and velocity-void spikes (same as old gate) to stay fail-safe.
    """
    cfg = config or {}
    if direction not in ("BUY", "SELL"):
        return (False, 0.0, "indeterminate direction")
    want = 1.0 if direction == "BUY" else -1.0
    min_score = float(cfg.get("min_confluence_score", 0.65))

    score = 0.0

    # --- HARD REJECTS (same as before — fail-safe) ---
    if len(getattr(liq, "active_breaks", []) or []) > 0:
        return (False, 0.0, "structural break in progress (falling knife)")
    if getattr(vel, "is_unusual", False) and getattr(liq, "liquidity_void_active", False):
        return (False, 0.0, "velocity spike in liquidity void")
        
    # --- CALIBRATED MICROSTRUCTURE FILTERS ---
    # Filters out chasing spikes and enters only when stalled/absorbing
    max_vel_pct = float(cfg.get("entry_max_velocity_pct", 100.0))
    min_decay_ratio = float(cfg.get("entry_min_decay_ratio", 0.0))
    max_tick_eff = float(cfg.get("entry_max_tick_efficiency", 1.0))
    
    vel_pct = getattr(vel, "percentile", 50.0) or 50.0
    decay = getattr(vel, "decay_ratio", 1.0) or 1.0
    eff = getattr(vel, "tick_efficiency", 0.5) or 0.5
    
    if vel_pct > max_vel_pct:
        return (False, 0.0, f"entry velocity percentile too high ({vel_pct:.1f}% > {max_vel_pct:.1f}%)")
    if decay < min_decay_ratio:
        return (False, 0.0, f"decay ratio too low ({decay:.2f} < {min_decay_ratio:.2f})")
    if eff > max_tick_eff:
        return (False, 0.0, f"tick efficiency too high ({eff:.2f} > {max_tick_eff:.2f})")
    # Hard reject: counter-trend without any exhaustion (unchanged from old gate)
    h4b = mtf.h4_bias
    h1b = mtf.h1_bias
    mtf_warm = not (h4b == 0.0 and h1b == 0.0 and mtf.m15_bias == 0.0)
    revp = float(getattr(mtf, "reversal_pressure", 0.0) or 0.0)
    is_exhausting = (
        mtf.is_exhaustion_zone or mtf.is_pullback or revp >= 0.5
        or getattr(disp, "is_exhausting", False)
        or getattr(disp, "classification", "") in ("EXHAUSTION", "TRAP", "ABSORPTION")
    )
    if mtf_warm and (h4b + h1b) * want < 0 and not is_exhausting:
        return (False, 0.0, f"counter-trend without exhaustion (H4={h4b:.2f},H1={h1b:.2f})")

    # --- COMPONENT 1 (30%): Candle Setup Score ---
    score += 0.30 * min(1.0, candle_setup_score)

    # --- COMPONENT 2 (25%): Tick Velocity Exhaustion ---
    # Decay + climax efficiency (lower eff at extreme = more exhausted)
    vel_score = 0.0
    decay = getattr(vel, "decay_ratio", 1.0) or 1.0
    eff = getattr(vel, "tick_efficiency", 0.5) or 0.5
    is_decaying = getattr(vel, "is_decaying", False)
    if is_decaying:
        vel_score += 0.5
    if decay < 0.5:
        vel_score += min(0.5, (0.5 - decay))
    if eff < 0.2:
        vel_score += 0.3
    score += 0.25 * min(1.0, vel_score)

    # --- COMPONENT 3 (25%): S/R Level Proximity ---
    if price_levels and pip:
        max_prox_pips = max(5.0, 0.3 * (h1_atr / pip)) if h1_atr else 15.0
        side = "resistance" if direction == "SELL" else "support"
        best_prox = 0.0
        for lvl in price_levels:
            if not getattr(lvl, "is_active", False):
                continue
            lvl_dir = getattr(lvl, "direction", "")
            if lvl_dir not in (side, "current", ""):
                continue
            dist_pips = abs(price - lvl.price) / pip
            if dist_pips <= max_prox_pips:
                # Closer = higher score (1.0 at 0 pips, 0.0 at max_prox_pips)
                prox_score = 1.0 - (dist_pips / max_prox_pips)
                
                # --- DYNAMIC LEVEL CONFLUENCE SCORER ---
                strength = getattr(lvl, "strength", 0.4) or 0.4
                lvl_type = str(getattr(lvl, "level_type", "")).upper()
                tf = str(getattr(lvl, "timeframe", "")).upper()
                
                # Boost for major timeframes & daily levels
                if any(x in lvl_type for x in ["PDH", "PDL", "PWH", "PWL", "H4", "H1"]):
                    strength = max(strength, 0.8)
                
                # Boost for round numbers (.00 / .50)
                symbol_upper = str(cfg.get("symbol") or cfg.get("mt5_symbol") or "").upper()
                is_gold = "XAU" in symbol_upper
                is_jpy = "JPY" in symbol_upper
                
                if is_gold or is_jpy:
                    remainder = lvl.price % 0.50
                    is_round_number = remainder < 0.05 or remainder > 0.45
                else:
                    pips_val = round(lvl.price / pip) if pip else 0
                    is_round_number = (pips_val % 50 == 0) or ((pips_val + 1) % 50 == 0) or ((pips_val - 1) % 50 == 0)
                
                if is_round_number:
                    strength = max(strength, 0.70)
                
                # Boost for multiple touches
                touches = getattr(lvl, "touches", 1) or 1
                if touches >= 3:
                    strength = max(strength, 0.85)
                elif touches == 2:
                    strength = max(strength, 0.65)
                
                # Reject weak levels (< 0.70 strength)
                min_lvl_strength = float(cfg.get("min_level_strength", 0.70))
                if strength < min_lvl_strength:
                    continue
                
                prox_score = min(1.0, prox_score * (0.7 + 0.3 * strength))
                best_prox = max(best_prox, prox_score)
        score += 0.25 * best_prox

    # --- COMPONENT 4 (20%): H4/H1 Trend Alignment ---
    if mtf_warm:
        h4_align = (h4b + h1b) * want
        if h4_align > 0.3:
            score += 0.20  # with-trend entry = full credit
        elif h4_align > -0.1:
            score += 0.10  # neutral
        # counter-trend: 0 (but not rejected here if exhaustion confirmed above)
    else:
        score += 0.10   # unwarmed MTF: partial credit (fail-open)

    score = min(1.0, score)
    if score >= min_score:
        return (True, score, f"unified allow score={score:.2f}")
    return (False, score, f"below unified threshold ({score:.2f}<{min_score:.2f})")


# Keep old function name as alias so any external callers still work
_reversal_confluence_grade = _unified_confluence_score


class ReversalModel:
    """The unified Market-State-Aware Reversal Engine."""

    def __init__(self, pip_mult: float = 0.0001, config: Optional[dict] = None):
        self._pip = pip_mult
        self._config = config or {}
        
        # Instantiate Tier 1
        self.velocity = VelocityNormalizer(pip_mult=self._pip, config=self._config)
        self.displacement = DisplacementEngine(pip_mult=self._pip, config=self._config)
        
        # Instantiate Tier 2
        self.regime = RegimeEngine(pip_mult=self._pip)
        self.mtf = MTFContext(pip_mult=self._pip, config=self._config)
        self.liquidity = LiquidityEngine(pip_mult=self._pip)
        
        # Instantiate Tier 3
        self.entry = EntryStateMachine(pip_mult=self._pip, config=self._config)
        self.health = TradeHealthMonitor(pip_mult=self._pip, config=self._config)
        self.exit = AdaptiveExitManager(pip_mult=self._pip, config=self._config)
        self.phase_tracker = TradePhaseTracker(pip_mult=self._pip)
        stats_csv = None if self._config.get("backtest_mode", False) else "reports/exit_stats.csv"
        self.exit_stats = ExitStats(csv_path=stats_csv)

        # NEW: Tier 3 Lifecycle Engines
        self.location_engine = LocationEngine(pip_mult=self._pip, config=self._config)
        self.trade_state_engine = TradeStateEngine(pip_mult=self._pip, config=self._config)
        self.exit_engine = ExitEngine(legacy_exit_manager=self.exit, pip_mult=self._pip, config=self._config)
        
        # NEW: CandleSetupTracker — gates the EntryStateMachine on M15 confirmed setups
        self.candle_setup = CandleSetupTracker(
            config=self._config,
            pip=self._pip,
        )

        # Latest cached states to avoid recalculating unnecessarily
        self._last_regime_state = RegimeState()
        self._last_mtf_state = MTFState()
        self._last_liquidity_state = LiquidityState()
        self._last_vel_state = NormalizedVelocity()
        self._last_disp_state = DisplacementState()
        self._last_health_state = TradeHealth()
        
        # H1 ATR tracking (default 12 pips on FX, scaled for JPY/XAU)
        self._h1_tr_window = deque(maxlen=14)
        _sym = self._config.get("symbol", "").upper()
        self._h1_atr = 0.12 if ("JPY" in _sym or "XAU" in _sym) else 0.0012
        self._prev_h1_close = None

        self.latest_snapshot = None  # Populated on every tick by daemon.py (bug #3 fix)

        # Stash of latest synced levels for the reversal-confluence gate scan
        self._price_levels: List[PriceLevel] = []

    def sync_levels(self, price_levels: List[PriceLevel]) -> None:
        """Update structural support/resistance levels."""
        self.liquidity.sync_levels(price_levels)
        # Reuse the same call for the confluence gate (only new data plumbing)
        self._price_levels = price_levels or []

    def on_candle_close(self, candle: LiveCandle) -> None:
        """Process a completed candle."""
        # 1. Update MTF context (accepts all timeframes)
        self._last_mtf_state = self.mtf.update_candle(candle)
        
        # 2. Track H1 ATR
        if candle.timeframe.upper() == "H1":
            tr = candle.high - candle.low
            if getattr(self, "_prev_h1_close", None) is not None:
                tr = max(tr, abs(candle.high - self._prev_h1_close), abs(candle.low - self._prev_h1_close))
            self._prev_h1_close = candle.close
            self._h1_tr_window.append(tr)
            if len(self._h1_tr_window) >= 14:
                self._h1_atr = sum(self._h1_tr_window) / 14
        
        # 3. Update Regime engine (M15 only to reduce noise)
        if candle.timeframe.upper() == "M15":
            self._last_regime_state = self.regime.update(candle, self._last_vel_state, self._last_disp_state)

        # 4. Feed CandleSetupTracker (M15 + H1 for engulfing detection)
        self.candle_setup.on_candle_close(
            candle=candle,
            price_levels=self._price_levels,
            h4_bias=self._last_mtf_state.h4_bias,
            h1_atr=self._h1_atr,
            pip=self._pip,
        )
        # Expire stale setups
        self.candle_setup.expire_if_stale()

    def on_tick(
        self,
        price: float,
        timestamp: datetime,
        volume: float = 1.0,
        location_context: Optional[LocationContext] = None,  # NEW: FIX 2 - optional param
        displacement_normalizer=None,  # Optional: DisplacementNormalizer instance
        session: Optional[str] = None,  # Canonical session label for velocity baselines
        bid: Optional[float] = None,
        ask: Optional[float] = None,
    ) -> EngineSnapshot:
        """
        Process a new tick through the entire pipeline.

        Args:
            price: Current market price
            timestamp: Tick time
            volume: Tick volume
            location_context: Optional LocationContext (if None, computed internally as fallback)
            session: Optional canonical session label (from daemon)
            bid: Optional tick bid price
            ask: Optional tick ask price
        """
        ts_float = timestamp.timestamp() if isinstance(timestamp, datetime) else float(timestamp)

        # --- TIER 1: DATA PIPELINE ---
        vel_state = self.velocity.update(price, timestamp, volume, regime=self._last_regime_state, session=session)
        self._last_vel_state = vel_state
        disp_state = self.displacement.update(
            price, timestamp, volume, vel_state,
            displacement_normalizer=displacement_normalizer,
            regime=self._last_regime_state  # Dynamic threshold adaptation
        )
        self._last_disp_state = disp_state

        # Populate MTF reversal_pressure from tick-level velocity decay +
        # displacement exhaustion (MTFContext itself has no tick data). This is
        # the reversal/exhaustion signal the EMA-slope bias structurally lacks;
        # the entry gate uses it to allow a genuine counter-trend reversal.
        try:
            rp = 0.0
            if getattr(vel_state, "is_decaying", False):
                rp += 0.5
            dr = getattr(vel_state, "decay_ratio", 1.0)
            if dr is not None and dr < 0.5:
                rp += min(0.5, (0.5 - dr))
            if getattr(disp_state, "is_exhausting", False) or getattr(disp_state, "classification", "") in ("EXHAUSTION", "TRAP", "ABSORPTION"):
                rp += 0.5
            self._last_mtf_state.reversal_pressure = min(1.0, rp)
        except Exception:
            pass

        # --- TIER 2: ANALYSIS PIPELINE ---
        liq_state = self.liquidity.update(price, timestamp, vel_state, disp_state)
        self._last_liquidity_state = liq_state

        # NEW: Compute location context if not provided (FIX 2 fallback)
        if location_context is None:
            location_context = self.location_engine.compute(
                price=price,
                atr_14_h1=self._h1_atr,
                recent_candles=[],  # Would be populated from daemon if needed
                price_levels=[],  # Would be populated from daemon if needed
            )

        # --- TIER 3: EXECUTION PIPELINE ---

        # 1. Evaluate Entry (pass candle setup state to gate the machine)
        spread = (ask - bid) if (bid is not None and ask is not None) else 0.0001
        entry_decision = self.entry.evaluate(
            price, timestamp, vel_state, disp_state, self._last_liquidity_state,
            self._last_regime_state, self._last_mtf_state,
            spread=spread,
            candle_setup_active=self.candle_setup.setup_active,
            candle_setup_direction=self.candle_setup.setup_direction,
        )

        # 1b. UNIFIED CONFLUENCE GATE (replaces old _reversal_confluence_grade).
        # Uses 4-component weighted score: candle setup (30%) + velocity (25%)
        # + S/R proximity (25%) + H4/H1 alignment (20%). Threshold: 0.65.
        # Hard-rejects (falling knife, void spike, counter-trend) preserved.
        if entry_decision.is_valid_entry and self._config.get("enable_reversal_gate", True):
            allow, score, reason = _unified_confluence_score(
                entry_decision.direction, price, self._pip, self._h1_atr,
                self._last_mtf_state, self._last_liquidity_state,
                vel_state, disp_state, self._price_levels,
                candle_setup_score=self.candle_setup.setup_score,
                config=self._config,
            )
            # Carry the confluence score for sizing/observability regardless.
            entry_decision.signal_quality = max(entry_decision.signal_quality, round(score, 2))
            if not allow:
                entry_decision.is_valid_entry = False
                entry_decision.skip_reason = reason
                entry_decision.reason = f"GATE_SKIP: {reason}"
                logger.info("REVERSAL GATE veto dir=%s: %s", entry_decision.direction, reason)
            else:
                logger.info("REVERSAL GATE allow dir=%s: %s", entry_decision.direction, reason)

        # 2. Evaluate Active Trade Health
        phase_snap = self.phase_tracker.update(price, vel_state, disp_state, liq_state)

        health_state = self.health.evaluate(
            price, ts_float, vel_state, disp_state, self._last_regime_state, self._last_mtf_state, phase_snap.phase
        )
        self._last_health_state = health_state

        # Build a tick snapshot carrying the full tier context. Used by both
        # trade_state_engine (velocity/displacement) and exit_engine's legacy
        # AdaptiveExitManager fallback (regime/liquidity/health/phase/mtf/atr).
        temp_snapshot = type("obj", (object,), {
            "velocity": vel_state,
            "displacement": disp_state,
            "regime": self._last_regime_state,
            "liquidity": self._last_liquidity_state,
            "mtf": self._last_mtf_state,
            "trade_health": health_state,
            "phase": phase_snap.phase,
            "phase_confidence": phase_snap.confidence,
            "atr": self._h1_atr,
        })()

        # NEW: Update trade state with lifecycle phase tracking
        trade_state = self.trade_state_engine.on_tick(
            price=price,
            timestamp=timestamp,
            snapshot=temp_snapshot,
            location_context=location_context,
            htf_context=self._last_mtf_state.htf_context if hasattr(self._last_mtf_state, "htf_context") else "NEUTRAL",
        )

        # 3. Evaluate Exit Options (NEW: use new ExitEngine instead of legacy only)
        exit_signal = self.exit_engine.evaluate(
            trade_state=trade_state,
            snapshot=temp_snapshot,
            location_context=location_context,
            current_price=price,
        )

        # Convert ExitSignal to ExitDecision for backwards compatibility
        exit_decision = ExitDecision(
            should_exit=exit_signal.should_exit,
            action=exit_signal.action,
            reason=exit_signal.reason,
            suggested_sl=exit_signal.suggested_sl,
            suggested_tp=exit_signal.suggested_tp,
        )

        return EngineSnapshot(
            timestamp=ts_float,
            price=price,
            velocity=vel_state,
            displacement=disp_state,
            regime=self._last_regime_state,
            mtf=self._last_mtf_state,
            liquidity=self._last_liquidity_state,
            entry_decision=entry_decision,
            trade_health=health_state,
            exit_decision=exit_decision,
            # NEW: Include lifecycle fields
            trade_state=trade_state,
            location_context=location_context,
            atr=self._h1_atr,
        )

    def register_trade(self, ticket: int, direction: str, entry_price: float, sl: float, tp: float, reason: str = "") -> None:
        """Tell the engine a trade was executed so it can manage it."""
        ts_now = datetime.now()
        ts_float = ts_now.timestamp()

        self.health.register_trade(
            ticket, direction, entry_price, ts_float, self._last_regime_state.regime, self._last_mtf_state.alignment_score
        )
        is_sweep = "sweep" in reason.lower()
        self.exit.register_trade(ticket, direction, entry_price, sl, tp, is_sweep=is_sweep)
        self.phase_tracker.register_trade(direction=direction, entry_price=entry_price, initial_confidence=80.0)

        # NEW: Register with trade_state_engine for lifecycle tracking
        self.trade_state_engine.register_trade(
            ticket=ticket,
            direction=direction,
            entry_price=entry_price,
            entry_time=ts_now,
            entry_sl=sl,
            entry_tp=tp,
            entry_reason=reason,
            position_size=0.01,  # Default, will be overridden by daemon if known
            entry_regime=self._last_regime_state.regime if self._last_regime_state else "UNKNOWN",
            entry_velocity_percentile=self._last_vel_state.percentile if self._last_vel_state else 0.0,
            entry_displacement_class=self._last_disp_state.classification if self._last_disp_state else "NEUTRAL",
        )
        
        # Reset entry machine to prevent rapid re-entries
        self.entry.reset()

    def clear_trade(self) -> None:
        """Tell the engine a trade was closed."""
        self.health.clear()
        self.exit.clear()
        self.phase_tracker.clear()
        self.trade_state_engine.reset()  # Reset trade state engine too!

    @property
    def config(self) -> dict:
        """Expose config for update propagation."""
        return self._config



__all__ = ["ReversalModel", "EngineSnapshot"]
