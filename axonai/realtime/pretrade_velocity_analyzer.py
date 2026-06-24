"""Pre-trade velocity baseline analysis.

Tracks session velocity patterns before trade entry to:
1. Establish baseline (mean, std) of normal velocity
2. Qualify entries only on z-score spikes (> 2.0)
3. Reject weak entries before they waste capital
"""

from __future__ import annotations
from collections import deque
from dataclasses import dataclass
import math


@dataclass
class VelocityBaseline:
    """Snapshot of velocity behavior before trade entry."""
    mean_velocity: float = 0.0
    std_velocity: float = 0.0
    peak_velocity: float = 0.0
    zscore_threshold: float = 2.0


class PreTradeVelocityAnalyzer:
    """Track velocity baseline and qualify entries on impulse strength."""

    def __init__(self, window_size: int = 100, pip_mult: float = 0.0001):
        self._pip = pip_mult
        self._window_size = window_size
        self._velocity_history = deque(maxlen=window_size)

    def add_velocity_sample(self, velocity: float):
        """Track velocity every tick (pips/sec)."""
        self._velocity_history.append(velocity)

    def get_baseline(self) -> VelocityBaseline:
        """Compute current baseline from history."""
        if len(self._velocity_history) < 10:
            return VelocityBaseline()

        velocities = list(self._velocity_history)
        mean_vel = sum(velocities) / len(velocities)
        variance = sum((v - mean_vel) ** 2 for v in velocities) / len(velocities)
        std_vel = math.sqrt(variance) if variance > 0 else 1e-10
        peak_vel = max(velocities)

        return VelocityBaseline(
            mean_velocity=mean_vel,
            std_velocity=std_vel,
            peak_velocity=peak_vel,
            zscore_threshold=2.0
        )

    def qualifies_for_entry(self, entry_velocity: float) -> tuple[bool, float]:
        """Check if entry velocity is strong enough impulse.

        Returns: (qualifies: bool, entry_zscore: float)
        """
        baseline = self.get_baseline()

        if baseline.mean_velocity == 0:
            return False, 0.0

        entry_zscore = (entry_velocity - baseline.mean_velocity) / (baseline.std_velocity + 1e-10)
        qualifies = entry_zscore >= baseline.zscore_threshold

        return qualifies, entry_zscore

    def reset(self):
        """Clear baseline after trade entry."""
        self._velocity_history.clear()


__all__ = ["PreTradeVelocityAnalyzer", "VelocityBaseline"]
