"""Root tick engine containing interfaces for high-frequency tick processing."""

from dataclasses import dataclass
import threading
from collections import deque
from typing import List, Optional

@dataclass(frozen=True)
class Tick:
    """Normalized MT5 tick."""
    bid: float
    ask: float
    last: float
    time_ms: int
    volume: int
    mid: float
    spread: float

class TickBuffer:
    """Thread-safe deque for storing ticks."""
    def __init__(self, maxlen: int = 10000):
        self._lock = threading.Lock()
        self._buffer = deque(maxlen=maxlen)

    def push(self, tick: Tick):
        with self._lock:
            self._buffer.append(tick)

    def snapshot(self) -> List[Tick]:
        with self._lock:
            return list(self._buffer)

    def last_n(self, n: int) -> List[Tick]:
        with self._lock:
            return list(self._buffer)[-n:]

@dataclass(frozen=True)
class SignalState:
    """Frozen dataclass snapshot of all current metrics."""
    timestamp_ms: int
    bid: float
    ask: float
    mid: float
    spread: float
    volume: int
    direction: str
    interval_ms: int
    spread_pips: float
    velocity: float
    imbalance_10s: float
    imbalance_60s: float
    imbalance_300s: float
    spread_delta: float
    velocity_collapse: bool
    aggression_shift: bool
    absorption: bool

class TickProcessor:
    """Computes classifications, measurements, and detections from raw ticks."""
    def __init__(self, max_history: int = 15000):
        self.history: deque[Tick] = deque(maxlen=max_history)
        self._lock = threading.Lock()
        self.last_signal_state: Optional[SignalState] = None

        # Tracking for trending/imbalance state checks
        self.imbalance_history: deque[float] = deque(maxlen=10)
        self.velocity_history: deque[float] = deque(maxlen=3000)  # for 5min average

    def process(self, tick: Tick) -> SignalState:
        with self._lock:
            prev_tick = self.history[-1] if self.history else None
            self.history.append(tick)

            # Prune ticks older than 300 seconds (300,000 ms) to keep buffers small and O(1) clean
            current_time_ms = tick.time_ms
            cutoff_300s = current_time_ms - 300000
            while self.history and self.history[0].time_ms < cutoff_300s:
                self.history.popleft()

            # Classify
            direction = "flat"
            interval_ms = 100
            if prev_tick:
                interval_ms = max(1, tick.time_ms - prev_tick.time_ms)
                if tick.mid > prev_tick.mid:
                    direction = "up"
                elif tick.mid < prev_tick.mid:
                    direction = "down"

            spread_pips = tick.spread / 0.0001

            # Get windows efficiently from the right (since history is sorted)
            cutoff_10s = current_time_ms - 10000
            cutoff_30s = current_time_ms - 30000
            cutoff_60s = current_time_ms - 60000

            history_list = list(self.history)
            n_ticks = len(history_list)

            # Scan from the right to find the index boundaries (extremely fast O(K))
            idx_10s = n_ticks - 1
            while idx_10s >= 0 and history_list[idx_10s].time_ms >= cutoff_10s:
                idx_10s -= 1
            idx_10s += 1

            idx_30s = n_ticks - 1
            while idx_30s >= 0 and history_list[idx_30s].time_ms >= cutoff_30s:
                idx_30s -= 1
            idx_30s += 1

            idx_60s = n_ticks - 1
            while idx_60s >= 0 and history_list[idx_60s].time_ms >= cutoff_60s:
                idx_60s -= 1
            idx_60s += 1

            recent_10s = history_list[idx_10s:]
            recent_30s = history_list[idx_30s:]
            recent_60s = history_list[idx_60s:]
            recent_300s = history_list

            # Measure (rolling time-based calculations)
            # Use last 10 seconds for velocity and imbalance calculations
            if len(recent_10s) > 1:
                price_changes = sum(abs(recent_10s[i].mid - recent_10s[i-1].mid) for i in range(1, len(recent_10s)))
                time_span = max(1, recent_10s[-1].time_ms - recent_10s[0].time_ms) / 1000.0
                raw_velocity = price_changes / time_span if time_span > 0 else 0.0
                velocity = raw_velocity / 0.0001
            else:
                velocity = 0.0

            # Store velocity for rolling average calculations
            self.velocity_history.append(velocity)

            # Imbalance score: normalized buy vs sell volume (-1.0 to 1.0)
            # Weighting volume by absolute price difference to correctly reflect momentum breakouts
            def calc_imbalance(window):
                if len(window) < 2:
                    return 0.0
                buy_vol = 0.0
                sell_vol = 0.0
                for i in range(1, len(window)):
                    diff = window[i].mid - window[i-1].mid
                    vol = max(1.0, float(window[i].volume))
                    if diff > 0:
                        buy_vol += vol * diff
                    elif diff < 0:
                        sell_vol += vol * abs(diff)
                total = buy_vol + sell_vol
                return (buy_vol - sell_vol) / total if total > 0 else 0.0

            imbalance_10s = calc_imbalance(recent_10s)
            imbalance_60s = calc_imbalance(recent_60s)
            imbalance_300s = calc_imbalance(recent_300s)

            spread_delta = tick.spread - (prev_tick.spread if prev_tick else tick.spread)

            # Detect
            # Velocity collapse: drop below 30% of average
            avg_velocity = sum(self.velocity_history) / len(self.velocity_history) if self.velocity_history else 0.0
            velocity_collapse = len(self.velocity_history) > 10 and velocity < (0.3 * avg_velocity)

            # Aggression shift: buy/sell pressure flipped rapidly
            aggression_shift = False
            if n_ticks > 10:
                cutoff_old_start = current_time_ms - 20000
                cutoff_old_end = current_time_ms - 10000
                
                idx_old_start = n_ticks - 1
                while idx_old_start >= 0 and history_list[idx_old_start].time_ms >= cutoff_old_start:
                    idx_old_start -= 1
                idx_old_start += 1

                idx_old_end = n_ticks - 1
                while idx_old_end >= 0 and history_list[idx_old_end].time_ms > cutoff_old_end:
                    idx_old_end -= 1
                idx_old_end += 1

                old_window = history_list[idx_old_start:idx_old_end]
                old_imbalance = calc_imbalance(old_window)
                aggression_shift = (imbalance_10s > 0.5 and old_imbalance < -0.5) or (imbalance_10s < -0.5 and old_imbalance > 0.5)

            # Absorption: high volume/velocity but low actual price movement over the last 30 seconds
            absorption = len(recent_30s) >= 10 and velocity > 0.01 and abs(recent_30s[-1].mid - recent_30s[0].mid) < 0.0001

            state = SignalState(
                timestamp_ms=tick.time_ms,
                bid=tick.bid,
                ask=tick.ask,
                mid=tick.mid,
                spread=tick.spread,
                volume=tick.volume,
                direction=direction,
                interval_ms=interval_ms,
                spread_pips=spread_pips,
                velocity=velocity,
                imbalance_10s=imbalance_10s,
                imbalance_60s=imbalance_60s,
                imbalance_300s=imbalance_300s,
                spread_delta=spread_delta,
                velocity_collapse=velocity_collapse,
                aggression_shift=aggression_shift,
                absorption=absorption
            )
            self.last_signal_state = state
            return state

@dataclass(frozen=True)
class ConfirmedSignal:
    """Pre-confirmed trading signal."""
    direction: str
    confidence: float

class DecisionEngine:
    """Combines signal and candle states to form a pre-confirmed signal."""
    def __init__(self):
        pass

    def evaluate(self, candle_snapshot, signal: SignalState) -> Optional[ConfirmedSignal]:
        # Basic heuristic for pre-confirmation
        if signal.imbalance_10s > 0.8 and signal.imbalance_60s > 0.5:
            return ConfirmedSignal(direction="long", confidence=0.8)
        elif signal.imbalance_10s < -0.8 and signal.imbalance_60s < -0.5:
            return ConfirmedSignal(direction="short", confidence=0.8)
        return None
