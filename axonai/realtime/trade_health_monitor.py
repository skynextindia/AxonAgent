"""Trade Health Monitor.

Continuously evaluates the "health" of an open position using displacement,
velocity, and regime context. Determines if the trade hypothesis is still
valid or if the market conditions have fundamentally changed against us.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from axonai.realtime.velocity_normalizer import NormalizedVelocity
from axonai.realtime.displacement_engine import (
    DisplacementState,
    DISPLACEMENT_IMPULSE,
    DISPLACEMENT_EXHAUSTION,
    DISPLACEMENT_TRAP,
    DISPLACEMENT_ABSORPTION,
    DISPLACEMENT_NEUTRAL,
)
from axonai.realtime.mtf_context import MTFState
from axonai.realtime.regime_engine import RegimeState
from axonai.realtime.trade_phase import TradePhase


ENERGY_PHASE_PENALTIES = {
    # (energy_state, phase) → score penalty
    ("ADVERSE_IMPULSE", "ENTRY_TRIGGERED"): 0.0,   # Grace period: no exit on immediate tick noise
    ("ADVERSE_IMPULSE", "EXPANSION"):       0.3,
    ("ADVERSE_IMPULSE", "CONTINUATION"):    0.2,
    ("ADVERSE_IMPULSE", "COMPRESSION"):     0.3,
    ("ADVERSE_IMPULSE", "EXHAUSTION"):      0.4,
    ("ADVERSE_IMPULSE", "REVERSAL_RISK"):   0.5,
    ("EXHAUSTING",      "CONTINUATION"):    0.3,
    ("EXHAUSTING",      "EXPANSION"):       0.1,   # could be a pause
    ("EXHAUSTING",      "EXHAUSTION"):      0.0,   # already in exhaustion — expected
    ("NOISE",           "*"):               0.0,   # never penalise for noise
}


@dataclass
class TradeHealth:
    """Output snapshot of trade health."""
    score: float = 1.0                # 0.0 (dead) to 1.0 (perfect)
    is_failing: bool = False          # True if score < 0.2
    time_in_drawdown_sec: float = 0.0 # Time spent underwater
    max_favorable_excursion: float = 0.0
    reason: str = "Healthy"


class TradeHealthMonitor:
    """Evaluates position health relative to its intended market state."""

    def __init__(self, pip_mult: float = 0.0001, config: Optional[dict] = None):
        self._pip = pip_mult
        self._config = config or {}
        self._backtest_mode = self._config.get("backtest_mode", False)
        
        # Position state
        self._is_active = False
        self._ticket: int = 0
        self._direction: str = ""
        self._entry_price: float = 0.0
        self._entry_time: float = 0.0
        
        # Metrics
        self._time_in_drawdown_sec = 0.0
        self._max_favorable_excursion = 0.0
        self._last_tick_time = 0.0
        self._consecutive_adverse = 0
        
        # Expected conditions (recorded at entry)
        self._entry_regime = ""
        self._entry_mtf_bias = 0.0

    def register_trade(self, ticket: int, direction: str, price: float, ts: float, regime: str, mtf_bias: float) -> None:
        """Register a new open position."""
        self._is_active = True
        self._ticket = ticket
        self._direction = direction.upper()
        self._entry_price = price
        self._entry_time = ts
        
        self._time_in_drawdown_sec = 0.0
        self._max_favorable_excursion = 0.0
        self._last_tick_time = ts
        self._consecutive_adverse = 0
        
        self._entry_regime = regime
        self._entry_mtf_bias = mtf_bias

    def clear(self) -> None:
        """Clear active trade state."""
        self._is_active = False
        self._consecutive_adverse = 0

    def _market_energy(
        self,
        vel: NormalizedVelocity,
        disp: DisplacementState,
    ) -> str:
        """
        Returns one of:
            HEALTHY_IMPULSE   — strong move in our direction
            ADVERSE_IMPULSE   — strong move against us
            EXHAUSTING        — move is dying
            NOISE             — high vel but no net displacement (trap/absorption)
        """
        cls = disp.classification

        if cls == DISPLACEMENT_IMPULSE:
            favour = (
                (self._direction == "BUY"  and disp.net_displacement_pips > 0) or
                (self._direction == "SELL" and disp.net_displacement_pips < 0)
            )
            if favour:
                self._consecutive_adverse = 0
                return "HEALTHY_IMPULSE"
            else:
                self._consecutive_adverse += 1
                limit = self._config.get("adverse_impulse_ticks", 2 if self._backtest_mode else 3)
                return "ADVERSE_IMPULSE" if self._consecutive_adverse >= limit else "NOISE"

        if cls == DISPLACEMENT_EXHAUSTION:
            self._consecutive_adverse = 0
            return "EXHAUSTING"

        # TRAP and ABSORPTION: high velocity but no net move — liquidity fight, not directional
        self._consecutive_adverse = 0
        return "NOISE"

    def evaluate(
        self,
        current_price: float,
        ts: float,
        velocity: NormalizedVelocity,
        displacement: DisplacementState,
        regime: RegimeState,
        mtf: MTFState,
        phase: TradePhase,
    ) -> TradeHealth:
        """Evaluate trade health based on current conditions."""
        if not self._is_active:
            return TradeHealth()
            
        dt = ts - self._last_tick_time
        # Squeezed-replay backtests jump ~15min per candle, so clamp big gaps to
        # one tick THERE. But in LIVE the old code also clamped any >5s gap to 1.0,
        # which made time_in_drawdown_sec massively undercount wall-clock during
        # quiet sessions (ticks ~20-60s apart) and neutralized the drawdown penalty.
        # Live: use the real elapsed time, capped at a sane 300s bound.
        if self._last_tick_time <= 0.0:
            dt = 0.0  # first tick after (re)activation
        elif self._backtest_mode:
            if dt > 5.0:
                dt = 1.0
        else:
            dt = min(max(dt, 0.0), 300.0)
        self._last_tick_time = ts
        
        # Calculate PnL in pips
        pips_profit = (current_price - self._entry_price) / self._pip
        if self._direction == "SELL":
            pips_profit = -pips_profit
            
        # Update excursions
        if pips_profit < 0:
            self._time_in_drawdown_sec += dt
        if pips_profit > self._max_favorable_excursion:
            self._max_favorable_excursion = pips_profit
            
        # Time in trade
        trade_duration = ts - self._entry_time
            
        # Baseline score starts at 1.0 and is reduced by warning signs
        score = 1.0
        reason = "Healthy"
        
        # 1. Stagnation / Time decay
        stagnation_limit = self._config.get("stagnation_limit", 2700)
        if trade_duration > stagnation_limit:
            # If we've been in a trade for an hour and barely moved, thesis is weak
            if self._max_favorable_excursion < 5.0 and pips_profit < 2.0:
                score -= 0.85
                reason = "Stagnant (Time Decay)"
                
        # 2. Drawdown duration
        # Dynamic drawdown limit based on market regime:
        # Trending regimes require tight cutting (30 mins), ranging/reversing regimes allow wider breathing room (60 mins)
        is_trending = regime.regime in ("TREND_EXPANSION", "TREND_CONTINUATION", "BREAKOUT")
        drawdown_limit_trending = self._config.get("drawdown_limit_trending", 2400)
        drawdown_limit_ranging = self._config.get("drawdown_limit_ranging", 2700)
        drawdown_limit = drawdown_limit_trending if is_trending else drawdown_limit_ranging
        if self._time_in_drawdown_sec > drawdown_limit:
            score -= 0.85
            reason = "Extended Drawdown"
            
        # 3. Energy/Phase-based health evaluation
        energy = self._market_energy(velocity, displacement)
        phase_val = phase.value if hasattr(phase, "value") else str(phase)
        penalty_key = (energy, phase_val)
        if penalty_key not in ENERGY_PHASE_PENALTIES:
            penalty_key = (energy, "*")
        penalty = ENERGY_PHASE_PENALTIES.get(penalty_key, 0.0)
        
        if penalty > 0.0:
            score -= penalty
            reason = f"{energy} in {phase_val}"
            
        # 4. Failed Breakout / Fakeout Return
        # Scale pip thresholds dynamically for Gold and JPY to accommodate volatility
        scale = 1.0
        if self._pip == 0.01:
            if self._entry_price > 1000.0:
                scale = 15.0  # Gold (e.g. 150 pips breakout, 30 pips drawdown)
            else:
                scale = 3.0   # JPY (e.g. 30 pips breakout, 6 pips drawdown)
                
        if self._max_favorable_excursion > (10.0 * scale) and pips_profit < (-2.0 * scale):
            score -= 0.85
            reason = "Failed Expansion (Full Retracement)"
            
        # 5. Regime Shift
        # E.g. we entered a TREND, but it shifted to CHOP or REVERSAL against us
        if self._entry_regime != regime.regime and trade_duration > 600:
            # Reversal against us is bad
            if regime.regime == "REVERSAL":
                if (self._direction == "BUY" and regime.trend_direction == "down") or \
                   (self._direction == "SELL" and regime.trend_direction == "up"):
                    score -= 0.4
                    reason = "Adverse Regime Shift (Reversal)"
                    
        score = max(0.0, score)
        
        return TradeHealth(
            score=round(score, 2),
            is_failing=score < 0.2,
            time_in_drawdown_sec=self._time_in_drawdown_sec,
            max_favorable_excursion=round(self._max_favorable_excursion, 1),
            reason=reason if score < 1.0 else "Healthy"
        )


__all__ = ["TradeHealthMonitor", "TradeHealth"]

