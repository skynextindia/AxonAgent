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
    # NEW: Include lifecycle fields
    trade_state: Optional[TradeState] = None
    location_context: Optional[LocationContext] = None
    atr: float = 0.0


# ── Reversal-Confluence Gate constants (fail-closed MTF + level veto) ─────
# Big-TF = daily/weekly/H4 structural levels. Micro intraday = session / M15 / H1
# structure. Used to require a MAJOR + MICRO confluence at the reversal price.
MAJOR_TFS = {"D1", "W1", "H4"}
MAJOR_TYPES = {"PDH", "PDL", "PWH", "PWL", "H4_SWING", "ASH", "ASL", "LDH", "LDL", "LNDH", "LNDL", "NYH", "NYL"}
MICRO_TFS = {"SESSION", "M15", "H1"}
MICRO_TYPES = {"ASH", "ASL", "LDH", "LDL", "ROUND", "LNDH", "LNDL", "NYH", "NYL", "TODAY_H", "TODAY_L"}


def _reversal_confluence_grade(
    direction: Optional[str],
    price: float,
    pip: float,
    h1_atr: float,
    mtf: MTFState,
    liq: LiquidityState,
    vel: NormalizedVelocity,
    disp: DisplacementState,
    price_levels: Optional[List[PriceLevel]],
    config: Optional[dict] = None,
):
    """Graded, FAIL-OPEN reversal filter. Returns (allow, score, reason).

    Intraday design (replaces the old fail-closed AND-veto that blocked all
    entries): only a few unambiguous conditions HARD-REJECT; everything else
    contributes to a 0..1 confluence score compared against min_confluence_score.
    Missing levels / unwarmed MTF LOWER the score, they never veto — so the
    machine keeps trading instead of freezing when data is thin.
    """
    cfg = config or {}
    if direction not in ("BUY", "SELL"):
        return (False, 0.0, "indeterminate direction")
    want = 1.0 if direction == "BUY" else -1.0

    # Weights (config-tunable) and threshold.
    w_mtf = float(cfg.get("gate_w_mtf", 0.25))
    w_major = float(cfg.get("gate_w_major", 0.20))
    w_micro = float(cfg.get("gate_w_micro", 0.15))
    w_confirm = float(cfg.get("gate_w_confirm", 0.30))
    w_revp = float(cfg.get("gate_w_reversal_pressure", 0.25))
    w_struct = float(cfg.get("gate_w_structure_break", 0.15))
    min_score = float(cfg.get("min_confluence_score", 0.35))
    revp_thresh = float(cfg.get("gate_reversal_pressure_min", 0.5))

    score = 0.0

    # --- HARD REJECT: falling knife (structural break in progress) ---
    if len(getattr(liq, "active_breaks", []) or []) > 0:
        return (False, 0.0, "structural break in progress (falling knife)")
    # --- HARD REJECT: raw velocity spike into a liquidity void ---
    if getattr(vel, "is_unusual", False) and getattr(liq, "liquidity_void_active", False):
        return (False, 0.0, "velocity spike in liquidity void")

    # --- MTF (graded; fail-open on unwarmed) ---
    h4b, h1b, m15b = mtf.h4_bias, mtf.h1_bias, mtf.m15_bias
    mtf_warm = not (h4b == 0.0 and h1b == 0.0 and m15b == 0.0)
    revp = float(getattr(mtf, "reversal_pressure", 0.0) or 0.0)
    struct_ok = bool(getattr(mtf, "structure_break", False)) and (getattr(mtf, "structure_break_dir", 0) == (1 if direction == "BUY" else -1))
    if mtf_warm:
        big_lean = h4b + h1b
        exhausted = mtf.is_exhaustion_zone or mtf.is_pullback or revp >= revp_thresh or struct_ok
        if big_lean * want < 0:
            # Counter-trend: allowed ONLY with a real reversal signal; else HARD REJECT.
            if not exhausted:
                return (False, 0.0, f"against big-TF trend w/o exhaustion (H4={h4b:.2f},H1={h1b:.2f})")
            score += w_mtf  # counter-trend but exhaustion-confirmed
        else:
            score += w_mtf  # with-trend
    # unwarmed MTF: no contribution, no veto (fail-open)

    # reversal_pressure contribution (scaled) + structure break
    score += w_revp * min(1.0, revp)
    if struct_ok:
        score += w_struct

    # --- LEVELS (graded; single strong level is enough, fail-open if none) ---
    if price_levels:
        side = "resistance" if direction == "SELL" else "support"
        conf_pips = max(3.0, 0.3 * (h1_atr / pip)) if pip else 3.0
        has_major = False
        has_micro = False
        for lvl in price_levels:
            if not getattr(lvl, "is_active", False):
                continue
            lvl_dir = getattr(lvl, "direction", "")
            if lvl_dir != side and lvl_dir != "current":
                continue
            if pip and abs(price - lvl.price) / pip > conf_pips:
                continue
            tf = getattr(lvl, "timeframe", "")
            lt = getattr(lvl, "level_type", "")
            if tf in MAJOR_TFS or lt in MAJOR_TYPES:
                has_major = True
            if tf in MICRO_TFS or lt in MICRO_TYPES:
                has_micro = True
        if has_major:
            score += w_major
        if has_micro:
            score += w_micro
    # no levels synced: fail-open (score just doesn't get the level bonus)

    # --- REVERSAL CONFIRMATION (graded) ---
    sharp = len(getattr(liq, "active_sweeps", []) or []) > 0 and (
        disp.is_exhausting or disp.classification in ("EXHAUSTION", "TRAP", "ABSORPTION")
    )
    decayed = getattr(vel, "is_decaying", False) and (
        disp.is_exhausting or disp.classification == "EXHAUSTION" or getattr(disp, "displacement_ratio", 1.0) < 0.3
    )
    if sharp or decayed:
        score += w_confirm

    score = min(1.0, score)
    if score >= min_score:
        return (True, score, f"graded allow score={score:.2f}")
    return (False, score, f"below confluence threshold ({score:.2f}<{min_score:.2f})")


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

        # 1. Evaluate Entry
        spread = (ask - bid) if (bid is not None and ask is not None) else 0.0001
        entry_decision = self.entry.evaluate(
            price, timestamp, vel_state, disp_state, self._last_liquidity_state, self._last_regime_state, self._last_mtf_state,
            spread=spread
        )

        # 1b. GRADED, FAIL-OPEN reversal-confluence gate (ENABLED). Hard-rejects
        # only falling knives / open-space spikes / counter-trend-without-exhaustion;
        # otherwise scores confluence (MTF + levels + confirmation + reversal_pressure
        # + structure break) and allows when score >= min_confluence_score. Missing
        # levels/MTF lower the score, never veto. Can be disabled via config
        # `enable_reversal_gate=False` for shadow comparison.
        if entry_decision.is_valid_entry and self._config.get("enable_reversal_gate", True):
            allow, score, reason = _reversal_confluence_grade(
                entry_decision.direction, price, self._pip, self._h1_atr,
                self._last_mtf_state, self._last_liquidity_state,
                vel_state, disp_state, self._price_levels, self._config,
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
