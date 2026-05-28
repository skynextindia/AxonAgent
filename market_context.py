"""Market context builder for structuring LLM inputs."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from tick_engine import SignalState
from market_state import MarketStateMachine

@dataclass(frozen=True)
class MarketContext:
    """Compressed, complete picture of current market conditions passed to the LLM."""
    symbol: str
    timestamp_ms: int
    session: str

    buy_pressure: float
    sell_pressure: float
    pressure_delta: float
    dominant_side: str
    imbalance_score: float

    velocity_expanding: bool
    velocity_collapsing: bool
    absorption_detected: bool
    aggression_shift: bool

    current_state: str
    state_confidence: float
    state_duration_sec: float
    previous_state: str

    sweep_detected: bool
    continuation_failed: bool
    structure_break: str
    retest_active: bool

    spread_pips: float
    spread_safe: bool
    volatility_expanding: bool

    confirmed_signal: bool
    signal_direction: str
    signal_confidence: float

class MarketContextBuilder:
    """Constructs MarketContext snapshorts from multiple live components."""
    def __init__(self):
        pass

    def build(self, symbol: str, signal_state: SignalState, state_machine: MarketStateMachine, candle_snapshot: Optional[dict] = None) -> MarketContext:
        """Assemble the complete context object."""
        # Session logic
        dt = datetime.fromtimestamp(signal_state.timestamp_ms / 1000.0, tz=timezone.utc)
        utc_hour = dt.hour + dt.minute / 60.0
        
        if 13.0 <= utc_hour < 16.0:
            session = "london_new_york_overlap"
        elif 8.0 <= utc_hour < 13.0:
            session = "london"
        elif 16.0 <= utc_hour < 21.0:
            session = "new_york"
        elif 0.0 <= utc_hour < 8.0:
            session = "tokyo"
        else:
            session = "sydney"

        # Pressures mapping (must sum to 1.0)
        imbalance = signal_state.imbalance
        sell_pressure = 1.0 / (imbalance + 1.0)
        buy_pressure = imbalance / (imbalance + 1.0)
        
        dominant_side = "neutral"
        if buy_pressure > 0.55:
            dominant_side = "buy"
        elif sell_pressure > 0.55:
            dominant_side = "sell"

        imbalance_score = min(10.0, max(0.0, imbalance))

        # Spread safety check
        spread_pips = signal_state.spread_pips
        clean_symbol = symbol.upper()
        if "EURUSD" in clean_symbol:
            spread_safe = spread_pips < 2.0
        elif "GBPUSD" in clean_symbol:
            spread_safe = spread_pips < 2.5
        else:
            spread_safe = spread_pips < 2.5

        # Candle structure (with safe defaults if None)
        cs = candle_snapshot or {}
        sweep_detected = cs.get("sweep_detected", False)
        continuation_failed = cs.get("continuation_failed", False)
        structure_break = cs.get("structure_break", "none")
        retest_active = cs.get("retest_active", False)

        # Pre-confirmed signal heuristic
        confirmed_signal = False
        signal_direction = "none"
        signal_confidence = 0.0
        
        if buy_pressure > 0.70:
            confirmed_signal = True
            signal_direction = "long"
            signal_confidence = buy_pressure
        elif sell_pressure > 0.70:
            confirmed_signal = True
            signal_direction = "short"
            signal_confidence = sell_pressure

        return MarketContext(
            symbol=symbol,
            timestamp_ms=signal_state.timestamp_ms,
            session=session,
            buy_pressure=buy_pressure,
            sell_pressure=sell_pressure,
            pressure_delta=signal_state.spread_delta / 0.0001, # Proxy delta pip change
            dominant_side=dominant_side,
            imbalance_score=imbalance_score,
            velocity_expanding=not signal_state.velocity_collapse,
            velocity_collapsing=signal_state.velocity_collapse,
            absorption_detected=signal_state.absorption,
            aggression_shift=signal_state.aggression_shift,
            current_state=state_machine.current_state.name,
            state_confidence=state_machine.state_probability,
            state_duration_sec=state_machine.state_duration_ms / 1000.0,
            previous_state=state_machine.previous_state.name,
            sweep_detected=sweep_detected,
            continuation_failed=continuation_failed,
            structure_break=structure_break,
            retest_active=retest_active,
            spread_pips=spread_pips,
            spread_safe=spread_safe,
            volatility_expanding=not signal_state.velocity_collapse,
            confirmed_signal=confirmed_signal,
            signal_direction=signal_direction,
            signal_confidence=signal_confidence
        )
