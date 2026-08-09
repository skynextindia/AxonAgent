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
from typing import Optional, TYPE_CHECKING

from axonai.realtime.velocity_normalizer import NormalizedVelocity
from axonai.realtime.displacement_normalizer import (
    Z_SCORE_IMPULSE_THRESHOLD,
    Z_SCORE_TRAP_THRESHOLD,
)
from axonai.realtime.displacement_buffer_engine import DisplacementBufferEngine

if TYPE_CHECKING:
    from axonai.realtime.regime_engine import RegimeState


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
        impulse_ratio_threshold: float = 0.60,
        trap_ratio_threshold: float = 0.25,
        compression_velocity_z: float = -0.5,
        config: Optional[dict] = None,
    ):
        self._pip = pip_mult
        self._window = window_ticks
        self._config = config or {}
        self._backtest_mode = self._config.get("backtest_mode", False)

        # Base thresholds (will be overridden by dynamic buffer if regime provided)
        self._impulse_threshold = impulse_ratio_threshold
        self._trap_threshold = trap_ratio_threshold
        self._compression_z = compression_velocity_z

        # Pair-scaled raw-pip thresholds (FX defaults; daemon injects XAU-scaled
        # values via config so these don't degenerate on gold). See daemon config.
        self._exhaustion_min_move = float(self._config.get("displacement_exhaustion_min_move_pips", 3.0))
        self._trend_net_pips = float(self._config.get("displacement_trend_net_pips", 2.0))

        # Dynamic threshold engine
        self._buffer_engine = DisplacementBufferEngine(config=config)
        self._last_regime: Optional[object] = None  # RegimeState, but avoid circular import
        self._regime_start_time: float = 0.0

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
        displacement_normalizer=None,  # Optional: DisplacementNormalizer instance
        regime: Optional["RegimeState"] = None,  # Optional: RegimeState for dynamic thresholds
    ) -> DisplacementState:
        """Process one tick and return displacement state.

        Args:
            price: Mid price
            timestamp: Tick timestamp
            volume: Tick volume
            velocity: NormalizedVelocity from VelocityNormalizer
            displacement_normalizer: Optional DisplacementNormalizer to compute z-scores

        Returns:
            DisplacementState for this tick.
        """
        ts = timestamp.timestamp() if isinstance(timestamp, datetime) else float(timestamp)
        self._ticks.append((price, ts, volume))

        # Late import to avoid circular dependency
        from axonai.realtime.regime_engine import RegimeState as RegimeStateClass

        # Compute dynamic thresholds based on regime (if provided)
        if regime and regime != self._last_regime:
            self._last_regime = regime
            self._regime_start_time = ts

        time_in_regime = int(ts - self._regime_start_time) if self._last_regime else 0

        if regime:
            dyn_thresh = self._buffer_engine.compute(
                regime=regime,
                regime_confidence=regime.confidence if regime else 0.5,
                time_in_regime_seconds=time_in_regime,
            )
            # Apply dynamic thresholds
            self._impulse_threshold = dyn_thresh.impulse_threshold
            self._trap_threshold = dyn_thresh.trap_threshold

        cutoff = ts - 300.0  # limit to 5 minutes to avoid spanning M15 candle gaps in backtests
        ticks = [t for t in self._ticks if t[1] >= cutoff]

        if len(ticks) < 5:
            return DisplacementState()

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

        # ── Extract z-score from normalizer if available ─────────
        disp_z_score = 0.0
        disp_z_avail = False
        if displacement_normalizer is not None:
            disp_norm = displacement_normalizer.update(displacement_ratio, ts)
            disp_z_score = disp_norm.z_score
            # Real availability flag: the normalizer only produces a meaningful
            # z once it has >=50 samples. Do NOT infer availability from the z's
            # SIGN (the old bug) -- a genuine trap has a negative z.
            disp_z_avail = disp_norm.sample_count >= 50

        # ── Classification ──────────────────────────────────────
        classification = self._classify(
            velocity, displacement_ratio, net_move, total_move, disp_z_score, disp_z_avail
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
        if net > self._trend_net_pips:
            return "bullish"
        elif net < -self._trend_net_pips:
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
        disp_z_score: float = 0.0,
        disp_z_avail: bool = False,
    ) -> str:
        """Multi-factor displacement classification.

        Decision matrix:
          High velocity (z>2) + Unusual displacement (z_score>=1.5) = IMPULSE
          High velocity (z>2) + Low displacement (z_score<=-1.5) = TRAP/ABSORPTION
          Low velocity + Low displacement + low efficiency = COMPRESSION
          Decaying velocity (decay<0.5) = EXHAUSTION
          Otherwise = NEUTRAL

        When z_score unavailable (z_score=0.0), falls back to static ratio thresholds.
        """
        z = velocity.z_score
        is_high_vel = velocity.is_unusual or z > 1.5
        is_low_vel = z < self._compression_z
        is_decaying = velocity.is_decaying

        # Priority 1: Exhaustion (velocity was high, now decaying)
        if is_decaying and total_move > self._exhaustion_min_move:
            return DISPLACEMENT_EXHAUSTION

        # Priority 2: Impulse (high velocity + unusual displacement)
        # Use z-score when the normalizer is warmed; else fall back to static ratio.
        if is_high_vel:
            if disp_z_avail:
                if disp_z_score >= Z_SCORE_IMPULSE_THRESHOLD:  # 1.5
                    return DISPLACEMENT_IMPULSE
            elif disp_ratio >= self._impulse_threshold:
                # Z-score unavailable (cold start), use static threshold
                return DISPLACEMENT_IMPULSE

        # Priority 3: Trap / Absorption (high velocity + LOW displacement)
        # A genuine trap has a NEGATIVE z (below-mean displacement). The old
        # `disp_z_score > 0.0` gate made this branch unreachable, silently reverting
        # trap detection to the static ratio; gate on real availability instead.
        if is_high_vel or self._backtest_mode:
            should_be_trap = False
            if disp_z_avail:
                if disp_z_score <= Z_SCORE_TRAP_THRESHOLD:  # -1.5
                    should_be_trap = True
            elif disp_ratio < self._trap_threshold:
                # Z-score unavailable, use static threshold
                should_be_trap = True

            if should_be_trap:
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
