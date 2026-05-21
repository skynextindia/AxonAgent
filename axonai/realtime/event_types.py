"""Event type definitions for the real-time trading engine."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Any


class EventPriority(Enum):
    LOW = 1       # Informational (session transition, spread change)
    MEDIUM = 2    # Structural (candle pattern at key level)
    HIGH = 3      # Actionable (level breach, BOS, sweep)
    CRITICAL = 4  # Immediate (panic spike, flash crash)


class EventType(Enum):
    CANDLE_CLOSE = "candle_close"
    LEVEL_BREACH = "level_breach"
    STRUCTURE_BREAK = "structure_break"
    SWEEP_DETECTED = "sweep_detected"
    VOLATILITY_SPIKE = "volatility_spike"
    SESSION_TRANSITION = "session_transition"
    SPREAD_CHANGE = "spread_change"
    CANDLE_PATTERN = "candle_pattern"
    REGIME_SHIFT = "regime_shift"
    MOMENTUM_DIVERGENCE = "momentum_divergence"


@dataclass
class MarketEvent:
    """A detected market event that may trigger graph execution."""
    event_type: EventType
    priority: EventPriority
    timestamp: datetime
    symbol: str
    price: float
    details: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return (f"[{self.priority.name}] {self.event_type.value} "
                f"@ {self.price:.5f} | {self.details}")


@dataclass
class LiveCandle:
    """An in-memory OHLCV candle built from raw ticks."""
    timeframe: str       # "M1", "M5", "M15", "H1"
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int          # tick count
    is_closed: bool = False

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_shadow(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_shadow(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open
