"""Broker-independent velocity normalization layer.

Converts raw tick data into relative, context-aware velocity metrics.
The core question changes from "Is velocity above X?" to
"Is this velocity unusual for current market conditions?"

All metrics are computed from price + time + volume alone — no
external API calls, no AI/LLM dependencies.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from axonai.realtime.regime_engine import RegimeState


@dataclass
class NormalizedVelocity:
    """Output snapshot of velocity normalization on every tick."""

    # ── Tick rate ────────────────────────────────────────────────
    tick_rate_10s: float = 0.0      # ticks per second (10s window)
    tick_rate_60s: float = 0.0      # ticks per second (60s window)
    tick_rate_300s: float = 0.0     # ticks per second (300s window)

    # ── Displacement velocity ───────────────────────────────────
    displacement_velocity: float = 0.0   # pips per second (net direction)
    abs_velocity: float = 0.0            # pips per second (total movement)

    # ── Efficiency ──────────────────────────────────────────────
    tick_efficiency: float = 0.0    # 0.0 (chop) to 1.0 (pure impulse)

    # ── Acceleration / Decay ────────────────────────────────────
    acceleration: float = 0.0      # Δ velocity / Δ time  (pips/s²)
    decay_ratio: float = 1.0       # current_vel / peak_vel (0-1)

    # ── Relative metrics ────────────────────────────────────────
    percentile: float = 50.0       # rank within rolling window (0-100)
    z_score: float = 0.0           # standard deviations from session mean
    velocity_ratio: float = 1.0    # current / 300s moving average

    # ── Composite flags ─────────────────────────────────────────
    is_unusual: bool = False        # percentile > 90 OR z_score > 2.0
    is_decaying: bool = False       # decay_ratio < 0.5
    is_accelerating: bool = False   # acceleration > 0 for 3+ ticks

    # ── Raw (debug) ─────────────────────────────────────────────
    raw_velocity: float = 0.0


class VelocityNormalizer:
    """Converts raw tick-by-tick data into normalized velocity metrics.

    Designed to be broker/feed agnostic:
    - Uses pip_mult to handle JPY vs non-JPY pairs
    - Uses rolling percentile windows, not fixed thresholds
    - Adapts to session characteristics via z-score baseline
    """

    def __init__(
        self,
        pip_mult: float = 0.0001,
        window: int = 1000,
        velocity_window_sec: float = 10.0,
        config: Optional[dict] = None,
    ):
        self._pip = pip_mult
        self._window = window
        self._vel_window_sec = velocity_window_sec
        self._config = config or {}
        self._backtest_mode = self._config.get("backtest_mode", False)

        # Dynamic velocity threshold engine
        from axonai.realtime.velocity_threshold_engine import VelocityThresholdEngine
        self._threshold_engine = VelocityThresholdEngine(config=config)
        self._last_regime: Optional["RegimeState"] = None
        self._regime_start_time: float = 0.0
        self._pct_threshold: float = 90.0
        self._z_threshold: float = 2.0

        # Rolling tick history: (price, timestamp_sec, volume)
        self._ticks: deque[tuple[float, float, float]] = deque(maxlen=window)

        # Rolling velocity history for percentile / z-score
        self._velocity_history: deque[float] = deque(maxlen=window)
        self._abs_velocity_history: deque[float] = deque(maxlen=window)
        self._sorted_abs_velocities: list[float] = []

        # Peak tracking for decay ratio
        self._peak_velocity: float = 0.0
        self._peak_decay_ticks: int = 0

        # Acceleration tracking
        self._prev_velocity: float = 0.0
        self._prev_timestamp: float = 0.0
        self._accel_direction_count: int = 0  # consecutive positive accel ticks

        # Session baseline (resets on session boundaries or after window fills)
        self._session_velocities: deque[float] = deque(maxlen=5000)
        self._session_sum: float = 0.0
        self._session_sum_sq: float = 0.0

    def update(
        self,
        price: float,
        timestamp: datetime,
        volume: float = 1.0,
        regime: Optional["RegimeState"] = None,
    ) -> NormalizedVelocity:
        """Process one tick and return normalized velocity state.

        Args:
            price: Mid price (bid+ask)/2
            timestamp: Tick timestamp (naive UTC or aware)
            volume: Tick volume (default 1)
            regime: Optional RegimeState for dynamic thresholds

        Returns:
            NormalizedVelocity snapshot for this tick.
        """
        ts = timestamp.timestamp() if isinstance(timestamp, datetime) else float(timestamp)

        # Compute dynamic velocity thresholds based on regime (if provided)
        if regime and regime != self._last_regime:
            self._last_regime = regime
            self._regime_start_time = ts

        time_in_regime = int(ts - self._regime_start_time) if self._last_regime else 0

        if regime:
            dyn_thresh = self._threshold_engine.compute(
                regime=regime,
                regime_confidence=regime.confidence if regime else 0.5,
                time_in_regime_seconds=time_in_regime,
            )
            # Apply dynamic thresholds
            self._pct_threshold = dyn_thresh.percentile_threshold
            self._z_threshold = dyn_thresh.z_score_threshold
            import logging
            logging.getLogger(__name__).debug(f"VelocityThreshold: regime={dyn_thresh.regime_name} pct_threshold={self._pct_threshold} z_threshold={self._z_threshold}")
        
        # Reset peak on large tick gap (context transition)
        dt = ts - self._prev_timestamp if self._prev_timestamp > 0 else 1.0
        if dt > 5.0:
            self._peak_velocity = 0.0
            self._peak_decay_ticks = 0

        self._ticks.append((price, ts, volume))

        if len(self._ticks) < 3:
            return NormalizedVelocity(raw_velocity=0.0)

        # ── Tick rates ──────────────────────────────────────────
        tick_rate_10s = self._tick_rate(ts, 10.0)
        tick_rate_60s = self._tick_rate(ts, 60.0)
        tick_rate_300s = self._tick_rate(ts, 300.0)

        # ── Displacement velocity (net pips/sec over window) ────
        disp_vel, abs_vel = self._displacement_velocity(ts)

        # ── Tick efficiency ─────────────────────────────────────
        efficiency = self._tick_efficiency(ts)

        # ── Acceleration ────────────────────────────────────────
        accel = 0.0
        dt = ts - self._prev_timestamp if self._prev_timestamp > 0 else 1.0
        if dt > 0 and self._prev_timestamp > 0:
            accel = (abs_vel - self._prev_velocity) / dt

        if accel > 0:
            self._accel_direction_count += 1
        else:
            self._accel_direction_count = 0

        self._prev_velocity = abs_vel
        self._prev_timestamp = ts

        # ── Decay ratio ─────────────────────────────────────────
        if abs_vel > self._peak_velocity:
            self._peak_velocity = abs_vel
            self._peak_decay_ticks = 0
        else:
            self._peak_decay_ticks += 1

        decay_ratio = abs_vel / self._peak_velocity if self._peak_velocity > 0 else 1.0

        # Slowly decay the peak reference (half-life = 200 ticks)
        if self._peak_decay_ticks > 50:
            self._peak_velocity *= 0.995

        # Update session running stats for z-score
        if len(self._session_velocities) >= 5000:
            evited = self._session_velocities.popleft()
            self._session_sum -= evited
            self._session_sum_sq -= evited * evited
        
        self._session_velocities.append(abs_vel)
        self._session_sum += abs_vel
        self._session_sum_sq += abs_vel * abs_vel

        # Update sorted list for percentile
        import bisect
        if len(self._abs_velocity_history) >= self._window:
            evited = self._abs_velocity_history.popleft()
            idx = bisect.bisect_left(self._sorted_abs_velocities, evited)
            if idx < len(self._sorted_abs_velocities) and self._sorted_abs_velocities[idx] == evited:
                self._sorted_abs_velocities.pop(idx)
        
        self._abs_velocity_history.append(abs_vel)
        bisect.insort(self._sorted_abs_velocities, abs_vel)
        self._velocity_history.append(abs_vel)

        # ── Percentile (rank among last N velocities) ───────────
        pct = self._percentile(abs_vel)

        # ── Z-score against session baseline ────────────────────
        z = self._z_score(abs_vel)

        # ── Velocity ratio (current / 300s average) ─────────────
        avg_300 = self._avg_velocity(ts, 300.0)
        vel_ratio = abs_vel / avg_300 if avg_300 > 0 else 1.0

        # ── Composite flags ─────────────────────────────────────
        is_unusual = pct > self._pct_threshold or z > self._z_threshold
        decay_ticks_threshold = 3 if self._backtest_mode else 10
        is_decaying = decay_ratio < 0.5 and self._peak_decay_ticks > decay_ticks_threshold
        is_accelerating = self._accel_direction_count >= 3

        return NormalizedVelocity(
            tick_rate_10s=round(tick_rate_10s, 2),
            tick_rate_60s=round(tick_rate_60s, 2),
            tick_rate_300s=round(tick_rate_300s, 2),
            displacement_velocity=round(disp_vel, 4),
            abs_velocity=round(abs_vel, 4),
            tick_efficiency=round(efficiency, 4),
            acceleration=round(accel, 6),
            decay_ratio=round(decay_ratio, 4),
            percentile=round(pct, 1),
            z_score=round(z, 2),
            velocity_ratio=round(vel_ratio, 2),
            is_unusual=is_unusual,
            is_decaying=is_decaying,
            is_accelerating=is_accelerating,
            raw_velocity=round(abs_vel, 4),
        )

    def reset_session(self) -> None:
        """Reset session baseline (call on session boundary)."""
        self._session_velocities.clear()
        self._session_sum = 0.0
        self._session_sum_sq = 0.0
        self._peak_velocity = 0.0
        self._peak_decay_ticks = 0

    def reset_peak(self) -> None:
        """Reset the peak velocity tracking (call when entering a trade)."""
        self._peak_velocity = 0.0
        self._peak_decay_ticks = 0

    # ── Internal calculations ───────────────────────────────────

    def _tick_rate(self, now: float, window_sec: float) -> float:
        """Ticks per second over the last `window_sec` seconds."""
        cutoff = now - window_sec
        count = sum(1 for _, t, _ in self._ticks if t >= cutoff)
        return count / window_sec if window_sec > 0 else 0.0

    def _displacement_velocity(self, now: float) -> tuple[float, float]:
        """Compute net and absolute velocity in pips/sec over the velocity window.

        Returns (signed_displacement_pips_per_sec, absolute_pips_per_sec).
        """
        cutoff = now - self._vel_window_sec
        window_ticks = [(p, t) for p, t, _ in self._ticks if t >= cutoff]

        if len(window_ticks) < 2:
            return 0.0, 0.0

        first_p, first_t = window_ticks[0]
        last_p, last_t = window_ticks[-1]
        elapsed = last_t - first_t

        if elapsed <= 0:
            return 0.0, 0.0

        # Net displacement (directional)
        net_move = (last_p - first_p) / self._pip
        disp_vel = net_move / elapsed

        # Absolute movement (total path)
        abs_move = sum(
            abs(window_ticks[i][0] - window_ticks[i - 1][0])
            for i in range(1, len(window_ticks))
        ) / self._pip
        abs_vel = abs_move / elapsed

        return disp_vel, abs_vel

    def _tick_efficiency(self, now: float) -> float:
        """Ratio of net displacement to total path (0 = chop, 1 = impulse)."""
        cutoff = now - self._vel_window_sec
        window_ticks = [p for p, t, _ in self._ticks if t >= cutoff]

        if len(window_ticks) < 2:
            return 0.0

        net = abs(window_ticks[-1] - window_ticks[0])
        total = sum(abs(window_ticks[i] - window_ticks[i - 1]) for i in range(1, len(window_ticks)))

        return net / total if total > 0 else 0.0

    def _percentile(self, value: float) -> float:
        """Rank `value` among the rolling velocity window (0-100)."""
        if len(self._sorted_abs_velocities) < 10:
            return 50.0
        import bisect
        below = bisect.bisect_left(self._sorted_abs_velocities, value)
        return 100.0 * below / len(self._sorted_abs_velocities)

    def _z_score(self, value: float) -> float:
        """Standard deviations from the session mean."""
        n = len(self._session_velocities)
        if n < 30:
            return 0.0
        mean = self._session_sum / n
        variance = (self._session_sum_sq / n) - (mean * mean)
        std = math.sqrt(max(0.0, variance)) if variance > 0 else 1e-10
        return (value - mean) / std

    def _avg_velocity(self, now: float, window_sec: float) -> float:
        """Average absolute velocity over the last `window_sec` seconds."""
        cutoff = now - window_sec
        window_ticks = [(p, t) for p, t, _ in self._ticks if t >= cutoff]

        if len(window_ticks) < 2:
            return 0.0

        elapsed = window_ticks[-1][1] - window_ticks[0][1]
        if elapsed <= 0:
            return 0.0

        abs_move = sum(
            abs(window_ticks[i][0] - window_ticks[i - 1][0])
            for i in range(1, len(window_ticks))
        ) / self._pip

        return abs_move / elapsed


__all__ = ["VelocityNormalizer", "NormalizedVelocity"]
