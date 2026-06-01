"""Live tick behavior analysis — velocity, imbalance, microstructure peaks, level interaction.

Receives raw ticks from the MT5 bridge and runs them through:
  - TickProcessor  (velocity, imbalance, aggression shifts, absorption)
  - PeakDetector   (microstructure exhaustion, local swings)
  - LevelBehaviorTracker (level attacks, rejections, absorption)

Results are written to a rolling JSON log for offline analysis.
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from axonai.realtime.peak_detector import PeakDetector, PeakSignal
from axonai.realtime.level_tracker import LevelBehaviorTracker
from tick_engine import Tick, TickProcessor, SignalState

logger = logging.getLogger(__name__)

# ── Rolling stats snapshot ──────────────────────────────────────────

@dataclass
class TickBehaviorSnapshot:
    """Serialisable snapshot of tick behaviour at a point in time."""
    timestamp: str                     # ISO-8601
    bid: float
    ask: float
    mid: float
    spread_pips: float
    velocity: float
    imbalance_10s: float
    imbalance_60s: float
    imbalance_300s: float
    spread_delta: float
    velocity_collapse: bool
    aggression_shift: bool
    absorption: bool

    # rolling window statistics (last 300 ticks)
    avg_spread_pips: float = 0.0
    max_spread_pips: float = 0.0
    min_spread_pips: float = 0.0
    avg_velocity: float = 0.0
    tick_rate_sec: float = 0.0        # ticks per second over last 10s

    # peak (if any fired on this tick)
    peak: Optional[dict] = None


# ── Main analyzer ──────────────────────────────────────────────────

class TickBehaviorAnalyzer:
    """Processes live ticks through multiple detectors and logs results.

    Usage:
        analyzer = TickBehaviorAnalyzer()
        analyzer.feed_tick(bid=1.12345, ask=1.12358, time_ms=..., volume=1)
    """

    def __init__(
        self,
        log_dir: str = "reports/tick_behavior",
        snapshot_interval: int = 5,     # write a snapshot every N ticks
        max_log_files: int = 20,        # keep at most this many log files
        pip_mult: float = 0.0001,
    ):
        self.pip_mult = pip_mult
        self._snapshot_interval = snapshot_interval
        self._tick_count = 0

        # Core detectors
        self.processor = TickProcessor(max_history=15000)
        self.peak_detector = PeakDetector(window_size=50, pip_mult=pip_mult)
        self.level_tracker = LevelBehaviorTracker(pip_mult=pip_mult)

        # Rolling window for stats (last 300 ticks)
        self._recent_spreads: deque[float] = deque(maxlen=300)
        self._recent_velocities: deque[float] = deque(maxlen=300)
        self._tick_times: deque[float] = deque(maxlen=100)  # timestamps in seconds

        # Logging
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._current_log: Optional[Path] = None
        self._log_fh = None  # file handle
        self._max_log_files = max_log_files

        # Snapshot callback (optional, e.g. for dashboard relay)
        self.on_snapshot: Optional[Callable[[TickBehaviorSnapshot], None]] = None

    # ── Public API ─────────────────────────────────────────────────

    def feed_tick(
        self,
        bid: float,
        ask: float,
        time_s: Optional[float] = None,
        volume: float = 1.0,
    ) -> Optional[TickBehaviorSnapshot]:
        """Process one tick through all detectors.

        Args:
            bid: Current bid price.
            ask: Current ask price.
            time_s: Unix timestamp in seconds. Falls back to wall clock.
            volume: Tick volume (default 1 if not reported).

        Returns:
            A TickBehaviorSnapshot if this tick is a snapshot tick, else None.
        """
        self._tick_count += 1
        now_s = time_s if time_s is not None else datetime.now().timestamp()
        ts_ms = int(now_s * 1000)
        mid = (bid + ask) / 2.0
        spread = ask - bid

        # Build Tick and run through TickProcessor
        tick = Tick(
            bid=bid, ask=ask, last=mid, time_ms=ts_ms,
            volume=max(1, int(volume)), mid=mid, spread=spread,
        )
        state: SignalState = self.processor.process(tick)
        self.last_state = state

        # Run through PeakDetector
        dt = datetime.fromtimestamp(now_s, tz=timezone.utc)
        peak_signal: Optional[PeakSignal] = self.peak_detector.update(mid, dt)

        # Run through LevelBehaviorTracker (no active levels → just updates)
        self.level_tracker.update(mid, bid, ask, dt, volume, active_levels=[])

        # Rolling window
        self._recent_spreads.append(state.spread_pips)
        self._recent_velocities.append(state.velocity)
        self._tick_times.append(now_s)

        # Take snapshot every N ticks
        if self._tick_count % self._snapshot_interval != 0:
            return None

        snapshot = self._build_snapshot(state, peak_signal)
        self._write_log(snapshot)

        if self.on_snapshot:
            self.on_snapshot(snapshot)

        return snapshot

    def get_recent_stats(self) -> dict:
        """Return current rolling statistics as a dict (for dashboard / debug)."""
        spreads = list(self._recent_spreads)
        vels = list(self._recent_velocities)
        return {
            "avg_spread_pips": round(sum(spreads) / len(spreads), 1) if spreads else 0.0,
            "max_spread_pips": round(max(spreads), 1) if spreads else 0.0,
            "min_spread_pips": round(min(spreads), 1) if spreads else 0.0,
            "avg_velocity": round(sum(vels) / len(vels), 2) if vels else 0.0,
            "tick_count": self._tick_count,
            "peak_count": 0,  # tracked internally by PeakDetector
        }

    def get_level_summary(self) -> dict:
        """Return level behavior summary."""
        return self.level_tracker.get_behavior_summary()

    def close(self):
        """Flush and close the current log file."""
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None
            self._current_log = None

    # ── Internal helpers ───────────────────────────────────────────

    def _build_snapshot(
        self, state: SignalState, peak: Optional[PeakSignal]
    ) -> TickBehaviorSnapshot:
        spreads = list(self._recent_spreads)
        vels = list(self._recent_velocities)

        # Tick rate over last 10 s
        tick_rate = 0.0
        if len(self._tick_times) > 1:
            window = self._tick_times[-1] - self._tick_times[0]
            if window > 0:
                tick_rate = len(self._tick_times) / window

        return TickBehaviorSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            bid=state.bid,
            ask=state.ask,
            mid=state.mid,
            spread_pips=state.spread_pips,
            velocity=state.velocity,
            imbalance_10s=state.imbalance_10s,
            imbalance_60s=state.imbalance_60s,
            imbalance_300s=state.imbalance_300s,
            spread_delta=state.spread_delta,
            velocity_collapse=state.velocity_collapse,
            aggression_shift=state.aggression_shift,
            absorption=state.absorption,
            avg_spread_pips=round(sum(spreads) / len(spreads), 1) if spreads else 0.0,
            max_spread_pips=round(max(spreads), 1) if spreads else 0.0,
            min_spread_pips=round(min(spreads), 1) if spreads else 0.0,
            avg_velocity=round(sum(vels) / len(vels), 2) if vels else 0.0,
            tick_rate_sec=round(tick_rate, 1),
            peak=peak.to_dict() if peak else None,
        )

    def _write_log(self, snapshot: TickBehaviorSnapshot) -> None:
        """Append one JSON line to the rolling log file."""
        # Rotate log file daily
        date_str = datetime.now().strftime("%Y%m%d")
        log_path = self._log_dir / f"tick_behavior_{date_str}.jsonl"

        if log_path != self._current_log:
            if self._log_fh:
                self._log_fh.close()
            self._current_log = log_path
            self._log_fh = open(log_path, "a", encoding="utf-8")
            self._prune_old_logs()

        self._log_fh.write(json.dumps(asdict(snapshot)) + "\n")
        self._log_fh.flush()

    def _prune_old_logs(self) -> None:
        """Remove oldest log files beyond max_log_files."""
        files = sorted(self._log_dir.glob("tick_behavior_*.jsonl"))
        while len(files) > self._max_log_files:
            files[0].unlink()
            files = files[1:]


__all__ = ["TickBehaviorAnalyzer", "TickBehaviorSnapshot"]
