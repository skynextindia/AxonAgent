"""Price displacement engine — measures movement achieved relative to activity.

Distinguishes genuine institutional aggression from traps and absorption.
Key insight: high velocity does NOT always mean directional conviction.

  High velocity + High displacement = IMPULSE  (genuine move)
  High velocity + Low displacement  = TRAP     (absorption / stop hunt)
  Low velocity  + Low displacement  = COMPRESSION (pre-expansion)
  Decaying vel  + Any displacement  = EXHAUSTION  (move ending)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from axonai.realtime.velocity_normalizer import NormalizedVelocity


# ── Classification constants ────────────────────────────────────────
DISPLACEMENT_IMPULSE = "IMPULSE"
DISPLACEMENT_ABSORPTION = "ABSORPTION"
DISPLACEMENT_TRAP = "TRAP"
DISPLACEMENT_EXHAUSTION = "EXHAUSTION"
DISPLACEMENT_COMPRESSION = "COMPRESSION"
DISPLACEMENT_NEUTRAL = "NEUTRAL"


@dataclass
class DisplacementState:
    """Output snapshot of displacement analysis on every tick."""

    # ── Core metrics ────────────────────────────────────────────
    net_displacement_pips: float = 0.0      # net price move in window
    total_activity: float = 0.0             # sum of absolute tick moves (pips)
    displacement_ratio: float = 0.0         # net / total (0-1)

    # ── Directional decomposition ───────────────────────────────
    buy_displacement: float = 0.0           # pips moved up
    sell_displacement: float = 0.0          # pips moved down
    buy_volume: float = 0.0                 # volume on up-ticks
    sell_volume: float = 0.0                # volume on down-ticks

    # ── Volume-weighted metrics ─────────────────────────────────
    volume_weighted_displacement: float = 0.0   # net volume * direction
    volume_imbalance: float = 0.0               # (buy_vol - sell_vol) / total

    # ── Classification ──────────────────────────────────────────
    classification: str = DISPLACEMENT_NEUTRAL

    # ── Composite flags ─────────────────────────────────────────
    is_genuine_aggression: bool = False     # IMPULSE confirmed
    is_trap: bool = False                   # high vel + low disp
    is_absorption: bool = False             # high tick density + no progress
    is_exhausting: bool = False             # velocity decaying with displacement


class DisplacementEngine:
    """Computes displacement metrics from raw tick data + velocity context.

    Designed to answer: "Is this price movement genuine or a trap?"

    Call ``update()`` on every tick with the latest NormalizedVelocity.
    """

    def __init__(
        self,
        pip_mult: float = 0.0001,
        window_ticks: int = 100,
        impulse_ratio_threshold: float = 0.6,
        trap_ratio_threshold: float = 0.25,
        compression_velocity_z: float = -0.5,
    ):
        self._pip = pip_mult
        self._window = window_ticks

        # Thresholds
        self._impulse_threshold = impulse_ratio_threshold
        self._trap_threshold = trap_ratio_threshold
        self._compression_z = compression_velocity_z

        # Rolling tick history: (price, timestamp_sec, volume)
        self._ticks: deque[tuple[float, float, float]] = deque(maxlen=window_ticks)

        # Rolling displacement history for trend detection
        self._displacement_history: deque[float] = deque(maxlen=200)
        self._classification_history: deque[str] = deque(maxlen=50)

    def update(
        self,
        price: float,
        timestamp: datetime,
        volume: float,
        velocity: NormalizedVelocity,
    ) -> DisplacementState:
        """Process one tick and return displacement state.

        Args:
            price: Mid price
            timestamp: Tick timestamp
            volume: Tick volume
            velocity: NormalizedVelocity from VelocityNormalizer

        Returns:
            DisplacementState for this tick.
        """
        ts = timestamp.timestamp() if isinstance(timestamp, datetime) else float(timestamp)
        self._ticks.append((price, ts, volume))

        if len(self._ticks) < 5:
            return DisplacementState()

        ticks = list(self._ticks)

        # ── Net displacement ────────────────────────────────────
        net_move = (ticks[-1][0] - ticks[0][0]) / self._pip
        total_move = sum(
            abs(ticks[i][0] - ticks[i - 1][0])
            for i in range(1, len(ticks))
        ) / self._pip

        displacement_ratio = abs(net_move) / total_move if total_move > 0 else 0.0

        # ── Directional decomposition ───────────────────────────
        buy_disp = 0.0
        sell_disp = 0.0
        buy_vol = 0.0
        sell_vol = 0.0

        for i in range(1, len(ticks)):
            delta = (ticks[i][0] - ticks[i - 1][0]) / self._pip
            vol = ticks[i][2]
            if delta > 0:
                buy_disp += delta
                buy_vol += vol
            elif delta < 0:
                sell_disp += abs(delta)
                sell_vol += vol

        # ── Volume-weighted displacement ────────────────────────
        total_vol = buy_vol + sell_vol
        volume_imbalance = (buy_vol - sell_vol) / total_vol if total_vol > 0 else 0.0
        vw_disp = net_move * (total_vol / max(len(ticks), 1))

        # ── Classification ──────────────────────────────────────
        classification = self._classify(
            velocity, displacement_ratio, net_move, total_move
        )

        self._displacement_history.append(net_move)
        self._classification_history.append(classification)

        # ── Composite flags ─────────────────────────────────────
        is_genuine = classification == DISPLACEMENT_IMPULSE
        is_trap = classification == DISPLACEMENT_TRAP
        is_absorption = classification == DISPLACEMENT_ABSORPTION
        is_exhausting = classification == DISPLACEMENT_EXHAUSTION

        return DisplacementState(
            net_displacement_pips=round(net_move, 2),
            total_activity=round(total_move, 2),
            displacement_ratio=round(displacement_ratio, 4),
            buy_displacement=round(buy_disp, 2),
            sell_displacement=round(sell_disp, 2),
            buy_volume=round(buy_vol, 1),
            sell_volume=round(sell_vol, 1),
            volume_weighted_displacement=round(vw_disp, 2),
            volume_imbalance=round(volume_imbalance, 4),
            classification=classification,
            is_genuine_aggression=is_genuine,
            is_trap=is_trap,
            is_absorption=is_absorption,
            is_exhausting=is_exhausting,
        )

    def get_recent_trend(self, lookback: int = 20) -> str:
        """Summarize recent displacement direction.

        Returns: "bullish", "bearish", or "mixed"
        """
        if len(self._displacement_history) < lookback:
            return "mixed"
        recent = list(self._displacement_history)[-lookback:]
        net = sum(recent)
        if net > 2.0:
            return "bullish"
        elif net < -2.0:
            return "bearish"
        return "mixed"

    def is_regime_shifting(self) -> bool:
        """Detect when displacement classification changes rapidly."""
        if len(self._classification_history) < 10:
            return False
        recent = list(self._classification_history)[-10:]
        unique = len(set(recent))
        return unique >= 3  # 3+ different classifications in 10 ticks = unstable

    # ── Internal classification logic ───────────────────────────

    def _classify(
        self,
        velocity: NormalizedVelocity,
        disp_ratio: float,
        net_move: float,
        total_move: float,
    ) -> str:
        """Multi-factor displacement classification.

        Decision matrix:
          High velocity (z>2) + High displacement (ratio>0.6) = IMPULSE
          High velocity (z>2) + Low displacement  (ratio<0.25) = TRAP/ABSORPTION
          Low velocity + Low displacement + low efficiency = COMPRESSION
          Decaying velocity (decay<0.5) = EXHAUSTION
          Otherwise = NEUTRAL
        """
        z = velocity.z_score
        is_high_vel = velocity.is_unusual or z > 1.5
        is_low_vel = z < self._compression_z
        is_decaying = velocity.is_decaying

        # Priority 1: Exhaustion (velocity was high, now decaying)
        if is_decaying and total_move > 3.0:
            return DISPLACEMENT_EXHAUSTION

        # Priority 2: Impulse (high velocity + high displacement)
        if is_high_vel and disp_ratio >= self._impulse_threshold:
            return DISPLACEMENT_IMPULSE

        # Priority 3: Trap / Absorption (high velocity + LOW displacement)
        if is_high_vel and disp_ratio < self._trap_threshold:
            # Distinguish trap from absorption by tick density
            if velocity.tick_efficiency < 0.15:
                return DISPLACEMENT_ABSORPTION
            return DISPLACEMENT_TRAP

        # Priority 4: Compression (low velocity + low everything)
        if is_low_vel and disp_ratio < 0.3 and velocity.tick_efficiency < 0.3:
            return DISPLACEMENT_COMPRESSION

        return DISPLACEMENT_NEUTRAL


__all__ = [
    "DisplacementEngine",
    "DisplacementState",
    "DISPLACEMENT_IMPULSE",
    "DISPLACEMENT_ABSORPTION",
    "DISPLACEMENT_TRAP",
    "DISPLACEMENT_EXHAUSTION",
    "DISPLACEMENT_COMPRESSION",
    "DISPLACEMENT_NEUTRAL",
]
