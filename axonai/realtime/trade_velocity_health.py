"""Trade velocity health monitoring.

During open trade:
1. Monitor velocity behavior vs baseline
2. Calculate z-score relative to session
3. Detect reversal factors (displacement, regime, MTF misalignment)
4. Score trade health (1.0 = perfect, 0.0 = dead)
"""

from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from enum import Enum
import math


class VelocityTrend(str, Enum):
    """Velocity direction over recent ticks."""
    ACCELERATING = "ACCELERATING"
    STABLE = "STABLE"
    DECAYING = "DECAYING"
    OSCILLATING = "OSCILLATING"
    UNKNOWN = "UNKNOWN"


@dataclass
class TradeVelocityHealth:
    """Trade health based on velocity + reversal factors."""
    health_score: float = 1.0           # 0.0 (dead) to 1.0 (perfect)
    velocity_zscore: float = 0.0        # Current velocity vs session baseline
    velocity_decay_ratio: float = 1.0   # Current / peak during trade
    velocity_trend: VelocityTrend = VelocityTrend.UNKNOWN
    reversal_risk: float = 0.0          # 0.0-1.0 probability
    reason: str = "Healthy"


class TradeVelocityHealthMonitor:
    """Monitor trade health based on velocity + market factors."""

    def __init__(self, pip_mult: float = 0.0001, window_size: int = 30):
        self._pip = pip_mult
        self._window_size = window_size

        # Trade state
        self._entry_velocity = 0.0
        self._trade_velocity_window = deque(maxlen=window_size)
        self._peak_trade_velocity = 0.0

        # Baseline from pre-entry
        self._baseline_mean = 0.0
        self._baseline_std = 1e-10

    def register_trade(
        self,
        entry_velocity: float,
        baseline_mean: float,
        baseline_std: float
    ):
        """Initialize tracking for new trade."""
        self._entry_velocity = entry_velocity
        self._baseline_mean = baseline_mean
        self._baseline_std = max(baseline_std, 1e-10)
        self._peak_trade_velocity = entry_velocity
        self._trade_velocity_window.clear()
        self._trade_velocity_window.append(entry_velocity)

    def evaluate(
        self,
        current_velocity: float,
        displacement_type: str,  # IMPULSE, EXHAUSTION, TRAP, etc.
        regime_shift: bool,      # Did regime change?
        mtf_alignment: float,    # -1.0 to +1.0
    ) -> TradeVelocityHealth:
        """Evaluate trade health based on velocity + market factors."""

        # 1. ADD TO WINDOW
        self._trade_velocity_window.append(current_velocity)

        # 2. PEAK TRACKING
        if current_velocity > self._peak_trade_velocity:
            self._peak_trade_velocity = current_velocity

        velocity_decay = current_velocity / (self._peak_trade_velocity + 1e-10)

        # 3. Z-SCORE vs BASELINE (not vs peak)
        velocity_zscore = (current_velocity - self._baseline_mean) / self._baseline_std

        # 4. VELOCITY TREND
        if len(self._trade_velocity_window) >= 3:
            recent = list(self._trade_velocity_window)[-3:]
            if recent[-1] > recent[-2] > recent[-3]:
                velocity_trend = VelocityTrend.ACCELERATING
            elif recent[-1] < recent[-2] < recent[-3]:
                velocity_trend = VelocityTrend.DECAYING
            elif abs(recent[-1] - recent[0]) < 0.001:
                velocity_trend = VelocityTrend.STABLE
            else:
                velocity_trend = VelocityTrend.OSCILLATING
        else:
            velocity_trend = VelocityTrend.UNKNOWN

        # 5. REVERSAL RISK FACTORS
        reversal_risk = 0.0
        reason = ""

        # Factor 1: Velocity collapse
        if velocity_decay < 0.5:
            reversal_risk += 0.3
            reason = f"Velocity collapsed to {velocity_decay:.0%}"

        # Factor 2: Exhaustion displacement
        if displacement_type == "EXHAUSTION":
            reversal_risk += 0.25
            reason = f"Displacement: {displacement_type}"

        # Factor 3: Regime shift
        if regime_shift:
            reversal_risk += 0.2
            reason = "Regime shifted"

        # Factor 4: Z-score back to baseline
        if velocity_zscore < 0.5:
            reversal_risk += 0.15
            reason = "Velocity back to baseline"

        # Factor 5: MTF misalignment
        if abs(mtf_alignment) < 0.2:
            reversal_risk += 0.1
            reason = "MTF alignment weak"

        # 6. HEALTH SCORE
        health_score = max(0.0, 1.0 - reversal_risk)

        if not reason:
            reason = f"Healthy (trend={velocity_trend.value})"

        return TradeVelocityHealth(
            health_score=health_score,
            velocity_zscore=velocity_zscore,
            velocity_decay_ratio=velocity_decay,
            velocity_trend=velocity_trend,
            reversal_risk=reversal_risk,
            reason=reason
        )

    def reset(self):
        """Clear trade state."""
        self._trade_velocity_window.clear()
        self._peak_trade_velocity = 0.0


__all__ = ["TradeVelocityHealthMonitor", "TradeVelocityHealth", "VelocityTrend"]
