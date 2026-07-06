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
    # NEW: Lifecycle fields
    trade_state: Optional[TradeState] = None
    location_context: Optional[LocationContext] = None


# ── Reversal-Confluence Gate constants (fail-closed MTF + level veto) ─────
# Big-TF = daily/weekly/H4 structural levels. Micro intraday = session / M15 / H1
# structure. Used to require a MAJOR + MICRO confluence at the reversal price.
MAJOR_TFS = {"D1", "W1", "H4"}
MAJOR_TYPES = {"PDH", "PDL", "PWH", "PWL", "H4_SWING"}
MICRO_TFS = {"SESSION", "M15", "H1"}
MICRO_TYPES = {"ASH", "ASL", "LDH", "LDL", "ROUND", "LNDH", "LNDL", "NYH", "NYL", "TODAY_H", "TODAY_L"}


def _reversal_confluence_veto(
    direction: Optional[str],
    price: float,
    pip: float,
    h1_atr: float,
    mtf: MTFState,
    liq: LiquidityState,
    vel: NormalizedVelocity,
    disp: DisplacementState,
    price_levels: Optional[List[PriceLevel]],
) -> Optional[str]:
    """Fail-closed reversal gate. Returns the FIRST skip_reason string, or None to allow.

    Runs five ordered checks; any missing/ambiguous/unwarmed data => SKIP (never fire blind).
    """
    # GATE 1 - DIRECTION SANITY (fail-closed)
    if direction not in ("BUY", "SELL"):
        return "indeterminate direction"

    want = 1.0 if direction == "BUY" else -1.0

    # GATE 2 - MTF warm check + FADE-AT-EXTREMES context (fail-closed on unwarmed)
    h4b, h1b, m15b = mtf.h4_bias, mtf.h1_bias, mtf.m15_bias
    # Truly unwarmed only when ALL three biases are exactly 0.0 (EMA-None sentinel
    # from _calculate_tf_bias). A warm-but-flat market keeps at least one non-zero
    # bias, so it is NOT falsely blocked here.
    if h4b == 0.0 and h1b == 0.0 and m15b == 0.0:
        return "MTF not warm (all bias 0)"
    # Fade-at-extremes policy: WITH-trend entries are always allowed; AGAINST the
    # big-TF trend is allowed ONLY when the HTF move is exhausted / pulling back
    # (a real reversal, not a falling knife). Open-space counter-trend is further
    # blocked by GATE 3 (must be at a level) + GATE 4 (must show exhaustion/sweep).
    big_lean = h4b + h1b
    if big_lean * want < 0 and not (mtf.is_exhaustion_zone or mtf.is_pullback):
        return f"against big-TF trend w/o exhaustion (H4={h4b:.2f},H1={h1b:.2f})"

    # GATE 3 - MAJOR+MICRO LEVEL CONFLUENCE (no open-space / mid-move entries)
    if not price_levels:
        return "no levels synced (open space)"
    side = "resistance" if direction == "SELL" else "support"  # fade resistance / bounce support
    conf_pips = max(3.0, 0.3 * (h1_atr / pip))  # ATR-scaled 'at a level' window
    has_major = False
    has_micro = False
    for lvl in price_levels:
        if not getattr(lvl, "is_active", False):
            continue
        # Include the level price is sitting ON: within ~2 pips it gets relabeled
        # "current" (not support/resistance), yet that is exactly the level being
        # reversed. Accept "current" alongside the correct fade side.
        lvl_dir = getattr(lvl, "direction", "")
        if lvl_dir != side and lvl_dir != "current":
            continue
        if abs(price - lvl.price) / pip > conf_pips:
            continue
        tf = getattr(lvl, "timeframe", "")
        lt = getattr(lvl, "level_type", "")
        if tf in MAJOR_TFS or lt in MAJOR_TYPES:
            has_major = True
        if tf in MICRO_TFS or lt in MICRO_TYPES:
            has_micro = True
    if not has_major:
        return "no MAJOR level at price (open space)"
    if not has_micro:
        return "no MICRO level confluence"

    # GATE 4 - REVERSAL CONFIRMATION AT LEVEL (no falling knives; fail-closed)
    # Falling-knife veto: an accepted/broken level = continuation, never fade it
    if len(liq.active_breaks) > 0:
        return "structural break in progress (falling knife)"
    # SHARP: liquidity sweep at level + displacement flip/exhaustion
    sharp = len(liq.active_sweeps) > 0 and (
        disp.is_exhausting or disp.classification in ("EXHAUSTION", "TRAP", "ABSORPTION")
    )
    # DECAYED: momentum decay/exhaustion at level
    decayed = vel.is_decaying and (
        disp.is_exhausting or disp.classification == "EXHAUSTION" or disp.displacement_ratio < 0.3
    )
    if not (sharp or decayed):
        return "no sharp/decayed reversal confirmation (knife)"

    # GATE 5 - RAW-SPIKE-IN-VOID BACKSTOP (belt-and-suspenders)
    if vel.is_unusual and liq.liquidity_void_active:
        return "velocity spike in liquidity void"

    return None  # all checks passed -> allow the reversal


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
        spread = (ask - bid) if (bid is not None and ask is not None) else 0.0001
        entry_decision = self.entry.evaluate(
            price, timestamp, vel_state, disp_state, self._last_liquidity_state, self._last_regime_state, self._last_mtf_state,
            spread=spread
        )

        # 1b. FAIL-CLOSED reversal-confluence gate — DISABLED (was blocking all entries).
        # Re-enable by uncommenting when level/MTF data pipeline is verified.
        # if entry_decision.is_valid_entry:
        #     skip = _reversal_confluence_veto(
        #         entry_decision.direction, price, self._pip, self._h1_atr,
        #         self._last_mtf_state, self._last_liquidity_state,
        #         vel_state, disp_state, self._price_levels,
        #     )
        #     if skip:
        #         entry_decision.is_valid_entry = False
        #         entry_decision.skip_reason = skip
        #         entry_decision.reason = f"GATE_SKIP: {skip}"  # observable in dashboard/logs
        #         logger.info("REVERSAL GATE veto dir=%s: %s", entry_decision.direction, skip)

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
