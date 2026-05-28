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
    imbalance: float
    spread_delta: float
    velocity_collapse: bool
    aggression_shift: bool
    absorption: bool

class TickProcessor:
    """Computes classifications, measurements, and detections from raw ticks."""
    def __init__(self, max_history: int = 3000):
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

            # Measure (simple rolling calculations)
            recent = list(self.history)[-50:]
            if len(recent) > 1:
                price_changes = sum(abs(recent[i].mid - recent[i-1].mid) for i in range(1, len(recent)))
                time_span = max(1, recent[-1].time_ms - recent[0].time_ms) / 1000.0
                raw_velocity = price_changes / time_span if time_span > 0 else 0.0
                velocity = raw_velocity / 0.0001
            else:
                velocity = 0.0

            # Store velocity for rolling average calculations
            self.velocity_history.append(velocity)

            # Imbalance score: simple volume pressure or price-movement based asymmetry
            buys = sum(1 for i in range(1, len(recent)) if recent[i].mid > recent[i-1].mid)
            sells = sum(1 for i in range(1, len(recent)) if recent[i].mid < recent[i-1].mid)
            imbalance = float(buys) / max(1.0, float(sells))

            spread_delta = tick.spread - (prev_tick.spread if prev_tick else tick.spread)

            # Detect
            # Velocity collapse: drop below 30% of average
            avg_velocity = sum(self.velocity_history) / len(self.velocity_history) if self.velocity_history else 0.0
            velocity_collapse = len(self.velocity_history) > 10 and velocity < (0.3 * avg_velocity)

            # Aggression shift: buy/sell pressure flipped rapidly
            aggression_shift = False
            if len(self.history) > 10:
                old_imbalance = sum(1 for t in list(self.history)[-20:-10] if t.mid > prev_tick.mid) / max(1, sum(1 for t in list(self.history)[-20:-10] if t.mid < prev_tick.mid))
                aggression_shift = (imbalance > 2.0 and old_imbalance < 0.5) or (imbalance < 0.5 and old_imbalance > 2.0)

            # Absorption: high volume/velocity but low actual price movement
            absorption = len(recent) >= 20 and velocity > 0.01 and abs(recent[-1].mid - recent[0].mid) < 0.0001

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
                imbalance=imbalance,
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
        if signal.imbalance > 3.0:
            return ConfirmedSignal(direction="long", confidence=0.8)
        elif signal.imbalance < 0.33:
            return ConfirmedSignal(direction="short", confidence=0.8)
        return None
