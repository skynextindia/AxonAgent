"""Displacement ratio normalization layer.

Converts raw displacement ratios into z-score metrics adapted to rolling
market conditions. Similar architecture to velocity_normalizer.py.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass


@dataclass
class NormalizedDisplacement:
    """Output snapshot of displacement normalization on every tick."""

    ratio: float = 0.0                  # Raw displacement ratio (0-1)
    z_score: float = 0.0                # (ratio - mean) / stdev
    rolling_mean: float = 0.0           # Mean of recent ratios
    rolling_stdev: float = 0.0          # Stdev of recent ratios
    sample_count: int = 0               # Number of samples in window
    percentile: float = 50.0            # Rank among recent ratios


class DisplacementNormalizer:
    """Computes z-scores for displacement ratios over rolling time windows.

    Designed to answer: "Is this displacement ratio unusual for current
    market conditions?" instead of "Is it > 0.60?"
    """

    def __init__(self, window_sec: float = 300.0):
        """
        Args:
            window_sec: Rolling time window in seconds (default 300 = 5 min)
        """
        self._window_sec = window_sec
        self._history: deque[tuple[float, float]] = deque()  # (ratio, timestamp)
        self._sum = 0.0
        self._sum_sq = 0.0

    def update(self, ratio: float, timestamp: float) -> NormalizedDisplacement:
        """Process one displacement ratio and return normalized state.

        Args:
            ratio: Displacement ratio (0-1, from DisplacementEngine)
            timestamp: Unix timestamp (seconds)

        Returns:
            NormalizedDisplacement with z_score and rolling stats
        """

        # Prune samples older than window
        cutoff = timestamp - self._window_sec
        while self._history and self._history[0][1] < cutoff:
            old_ratio, _ = self._history.popleft()
            self._sum -= old_ratio
            self._sum_sq -= old_ratio * old_ratio

        # Add new sample
        self._history.append((ratio, timestamp))
        self._sum += ratio
        self._sum_sq += ratio * ratio

        # Calculate rolling statistics
        n = len(self._history)

        if n < 50:
            # Insufficient data; return z_score=0 (neutral, use static thresholds)
            return NormalizedDisplacement(
                ratio=round(ratio, 4),
                z_score=0.0,
                rolling_mean=0.0,
                rolling_stdev=0.0,
                sample_count=n,
                percentile=50.0
            )

        # Calculate mean
        mean = self._sum / n

        # Calculate stdev (Welford method)
        variance = (self._sum_sq / n) - (mean * mean)
        stdev = math.sqrt(max(0.0, variance))

        # Calculate z-score
        z_score = 0.0
        if stdev > 1e-10:
            z_score = (ratio - mean) / stdev

        # Calculate percentile (simple: count how many are <= current)
        count_below = sum(1 for r, _ in self._history if r <= ratio)
        percentile = 100.0 * count_below / n if n > 0 else 50.0

        return NormalizedDisplacement(
            ratio=round(ratio, 4),
            z_score=round(z_score, 2),
            rolling_mean=round(mean, 4),
            rolling_stdev=round(stdev, 4),
            sample_count=n,
            percentile=round(percentile, 1)
        )

    def reset(self) -> None:
        """Clear rolling window (call on session boundary)."""
        self._history.clear()
        self._sum = 0.0
        self._sum_sq = 0.0


__all__ = ["DisplacementNormalizer", "NormalizedDisplacement"]
