from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Any
import pytz
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

    # Peak Detector integration
    peak_divergence_warning: bool
    peak_confirmed: bool  
    peak_confidence: float
    peak_dominant_side: str

    # Macro bridge integration (System 2 → System 1)
    macro_bias: str = "HOLD"
    macro_confidence: float = 0.0
    macro_key_level: float = 0.0
    macro_bias_age_sec: float = 999.0

class MarketContextBuilder:
    """Constructs MarketContext snapshorts from multiple live components."""
    _tz_london = pytz.timezone("Europe/London")
    _tz_ny = pytz.timezone("America/New_York")

    def __init__(self):
        pass

    @staticmethod
    def _classify_session(timestamp_ms: int) -> str:
        """Classify trading session using DST-aware timezone boundaries."""
        utc_dt = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=pytz.utc)
        london_hour = utc_dt.astimezone(MarketContextBuilder._tz_london).hour
        ny_hour = utc_dt.astimezone(MarketContextBuilder._tz_ny).hour

        london_active = 8 <= london_hour < 16
        ny_active = 8 <= ny_hour < 17

        if london_active and ny_active:
            return "london_new_york_overlap"
        elif london_active:
            return "london"
        elif ny_active:
            return "new_york"
        elif 0 <= utc_dt.hour < 9:
            return "tokyo"
        else:
            return "sydney"

    def build(self, symbol: str, signal_state: SignalState, state_machine: MarketStateMachine, candle_snapshot: Optional[dict] = None, peak_signal: Optional[Any] = None, macro_bridge=None) -> MarketContext:
        """Assemble the complete context object."""
        # DST-aware session classification
        session = self._classify_session(signal_state.timestamp_ms)

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

        # Extract peak signal details
        peak_divergence_warning = False
        peak_confirmed = False
        peak_confidence = 0.0
        peak_dominant_side = "none"

        if peak_signal is not None:
            if hasattr(peak_signal, "divergence_warning"):
                peak_divergence_warning = bool(peak_signal.divergence_warning)
                peak_confirmed = bool(peak_signal.peak_confirmed)
                peak_confidence = float(peak_signal.peak_confidence)
                peak_dominant_side = str(getattr(peak_signal, "dominant_side", "none"))
                if peak_dominant_side == "none" and hasattr(peak_signal, "direction"):
                    peak_dominant_side = "sell" if "bearish" in peak_signal.direction else "buy"
            elif isinstance(peak_signal, dict):
                peak_divergence_warning = bool(peak_signal.get("divergence_warning", False))
                peak_confirmed = bool(peak_signal.get("peak_confirmed", False))
                peak_confidence = float(peak_signal.get("peak_confidence", 0.0))
                peak_dominant_side = str(peak_signal.get("dominant_side", "none"))
                if peak_dominant_side == "none" and "direction" in peak_signal:
                    peak_dominant_side = "sell" if "bearish" in peak_signal["direction"] else "buy"

        # Extract macro bridge data (System 2 → System 1)
        macro_bias = "HOLD"
        macro_confidence = 0.0
        macro_key_level = 0.0
        macro_bias_age_sec = 999.0
        if macro_bridge is not None:
            macro_snap = macro_bridge.snapshot()
            if macro_snap is not None:
                macro_bias = macro_snap.bias
                macro_confidence = macro_snap.confidence
                macro_key_level = macro_snap.key_level
                macro_bias_age_sec = macro_snap.age_sec

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
            signal_confidence=signal_confidence,
            # Peak detector mapping
            peak_divergence_warning=peak_divergence_warning,
            peak_confirmed=peak_confirmed,
            peak_confidence=peak_confidence,
            peak_dominant_side=peak_dominant_side,
            # Macro bridge (System 2)
            macro_bias=macro_bias,
            macro_confidence=macro_confidence,
            macro_key_level=macro_key_level,
            macro_bias_age_sec=macro_bias_age_sec
        )
