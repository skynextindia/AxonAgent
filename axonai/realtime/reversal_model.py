"""Advanced Microstructure Reversal Model.

This is the top-level orchestrator that replaces `TickBehaviorAnalyzer` and
`PeakDetector`. It instantiates the 10 new modules and acts as the
primary entry point for `daemon.py` and `backtester.py`.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from axonai.realtime.live_state import PriceLevel
from axonai.realtime.event_types import LiveCandle

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
    # NEW: Lifecycle fields
    trade_state: Optional[TradeState] = None
    location_context: Optional[LocationContext] = None


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
        self.mtf = MTFContext(pip_mult=self._pip)
        self.liquidity = LiquidityEngine(pip_mult=self._pip)
        
        # Instantiate Tier 3
        self.entry = EntryStateMachine(pip_mult=self._pip)
        self.health = TradeHealthMonitor(pip_mult=self._pip, config=self._config)
        self.exit = AdaptiveExitManager(pip_mult=self._pip, config=self._config)
        self.phase_tracker = TradePhaseTracker(pip_mult=self._pip)
        stats_csv = None if self._config.get("backtest_mode", False) else "reports/exit_stats.csv"
        self.exit_stats = ExitStats(csv_path=stats_csv)

        # NEW: Tier 3 Lifecycle Engines
        self.location_engine = LocationEngine(pip_mult=self._pip, config=self._config)
        self.trade_state_engine = TradeStateEngine(pip_mult=self._pip, config=self._config)
        self.exit_engine = ExitEngine(legacy_exit_manager=self.exit, pip_mult=self._pip, config=self._config)
        
        # Latest cached states to avoid recalculating unnecessarily
        self._last_regime_state = RegimeState()
        self._last_mtf_state = MTFState()
        self._last_liquidity_state = LiquidityState()
        self._last_vel_state = NormalizedVelocity()
        self._last_disp_state = DisplacementState()
        self._last_health_state = TradeHealth()
        
        # H1 ATR tracking
        self._h1_tr_window = deque(maxlen=14)
        self._h1_atr = 0.0012
        self._prev_h1_close = None

        self.latest_snapshot = None  # Populated on every tick by daemon.py (bug #3 fix)

    def sync_levels(self, price_levels: List[PriceLevel]) -> None:
        """Update structural support/resistance levels."""
        self.liquidity.sync_levels(price_levels)

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

    def on_tick(
        self,
        price: float,
        timestamp: datetime,
        volume: float = 1.0,
        location_context: Optional[LocationContext] = None,  # NEW: FIX 2 - optional param
        displacement_normalizer=None,  # Optional: DisplacementNormalizer instance
    ) -> EngineSnapshot:
        """
        Process a new tick through the entire pipeline.

        Args:
            price: Current market price
            timestamp: Tick time
            volume: Tick volume
            location_context: Optional LocationContext (if None, computed internally as fallback)
        """
        ts_float = timestamp.timestamp() if isinstance(timestamp, datetime) else float(timestamp)

        # --- TIER 1: DATA PIPELINE ---
        vel_state = self.velocity.update(price, timestamp, volume, regime=self._last_regime_state)
        self._last_vel_state = vel_state
        disp_state = self.displacement.update(
            price, timestamp, volume, vel_state,
            displacement_normalizer=displacement_normalizer,
            regime=self._last_regime_state  # Dynamic threshold adaptation
        )
        self._last_disp_state = disp_state

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

        # 1. Evaluate Entry
        entry_decision = self.entry.evaluate(
            price, timestamp, vel_state, disp_state, self._last_liquidity_state, self._last_regime_state, self._last_mtf_state
        )

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

    @property
    def config(self) -> dict:
        """Expose config for update propagation."""
        return self._config



__all__ = ["ReversalModel", "EngineSnapshot"]
