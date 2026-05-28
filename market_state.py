"""Core market state machine for regime detection."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Dict
from tick_engine import SignalState

class MarketState(Enum):
    """The 7 official market microstructure states."""
    BALANCED = 1
    TRENDING = 2
    EXHAUSTING = 3
    SWEEPING = 4
    REVERSING = 5
    EXPANDING = 6
    COMPRESSING = 7

@dataclass
class StateTransition:
    """Represents a validated transition between two market states."""
    from_state: str
    to_state: str
    timestamp_ms: int
    confidence: float
    signal_snapshot: SignalState

class MarketStateMachine:
    """State machine tracking state transitions, durations, and probabilities."""
    def __init__(self, config: Optional[Dict] = None, debug: bool = False):
        self.debug = debug
        cfg = config or {}
        
        # Configurable thresholds
        self.compressing_velocity_ratio = cfg.get("compressing_velocity_ratio", 0.3) # 30% of average
        self.expanding_velocity_ratio = cfg.get("expanding_velocity_ratio", 2.0)     # 200% of compression baseline
        self.sweeping_velocity_ratio = cfg.get("sweeping_velocity_ratio", 3.0)       # 3x compression baseline
        self.sweeping_imbalance = cfg.get("sweeping_imbalance", 4.0)
        self.trending_imbalance = cfg.get("trending_imbalance", 2.5)
        self.balanced_imbalance = cfg.get("balanced_imbalance", 1.2)
        self.balanced_duration_ms = cfg.get("balanced_duration_ms", 60000)           # 60s
        self.trending_duration_ms = cfg.get("trending_duration_ms", 10000)           # 10s
        self.sweep_expiry_ms = cfg.get("sweep_expiry_ms", 30000)                     # 30s
        
        # Internal states
        self.current_state = MarketState.BALANCED
        self.previous_state = MarketState.BALANCED
        self.state_duration_ms = 0
        self.state_probability = 1.0
        
        # Tracking variables
        self.state_start_time = None
        self.compression_baseline_velocity = 0.0
        self.imbalance_history: List[float] = []
        self.balanced_candidate_start: Optional[int] = None
        self.trending_candidate_start: Optional[int] = None
        self.sweep_timestamp_ms: Optional[int] = None

    def update(self, signal: SignalState) -> Optional[StateTransition]:
        """Process a new tick signal and return a transition if one occurred."""
        timestamp = signal.timestamp_ms
        if self.state_start_time is None:
            self.state_start_time = timestamp

        self.state_duration_ms = timestamp - self.state_start_time
        
        # Track imbalance history for trending exhaustion check
        self.imbalance_history.append(signal.imbalance)
        if len(self.imbalance_history) > 10:
            self.imbalance_history.pop(0)

        # Transition checks
        next_state = self.current_state
        confidence = 0.8  # Default transition confidence
        
        # Compute 5min avg velocity (simulated/tracked inside signal or processor)
        # Processor's velocity_history tracks rolling average
        # Check transition logic
        if self.current_state == MarketState.BALANCED:
            # BALANCED -> COMPRESSING when: velocity falls below 30% of average
            if signal.velocity_collapse:
                next_state = MarketState.COMPRESSING
                self.compression_baseline_velocity = signal.velocity
                confidence = 0.85
            # BALANCED -> SWEEPING when: velocity_collapse=False AND imbalance > 4.0 AND sudden spike
            elif not signal.velocity_collapse and signal.imbalance > self.sweeping_imbalance:
                next_state = MarketState.SWEEPING
                self.sweep_timestamp_ms = timestamp
                confidence = 0.90

        elif self.current_state == MarketState.COMPRESSING:
            # COMPRESSING -> EXPANDING when: velocity rises above 200% of compression baseline
            baseline = max(0.0001, self.compression_baseline_velocity)
            if signal.velocity / baseline > self.expanding_velocity_ratio:
                next_state = MarketState.EXPANDING
                confidence = 0.80
            # COMPRESSING -> SWEEPING when: velocity spike > 3x compression baseline
            elif signal.velocity / baseline > self.sweeping_velocity_ratio:
                next_state = MarketState.SWEEPING
                self.sweep_timestamp_ms = timestamp
                confidence = 0.90

        elif self.current_state == MarketState.TRENDING:
            # TRENDING -> EXHAUSTING when: imbalance shrinking for 3 consecutive updates
            if len(self.imbalance_history) >= 3:
                last_three = self.imbalance_history[-3:]
                if last_three[0] > last_three[1] > last_three[2]:
                    next_state = MarketState.EXHAUSTING
                    confidence = 0.75

        elif self.current_state == MarketState.EXHAUSTING:
            # EXHAUSTING -> REVERSING when: aggression_shift=True AND velocity_collapse=True
            if signal.aggression_shift and signal.velocity_collapse:
                next_state = MarketState.REVERSING
                confidence = 0.85

        elif self.current_state == MarketState.SWEEPING:
            # SWEEPING -> REVERSING when: velocity_collapse=True within 30s of sweep
            if signal.velocity_collapse and self.sweep_timestamp_ms and (timestamp - self.sweep_timestamp_ms <= self.sweep_expiry_ms):
                next_state = MarketState.REVERSING
                confidence = 0.90
            # SWEEPING -> TRENDING when: no velocity collapse within 30s
            elif self.sweep_timestamp_ms and (timestamp - self.sweep_timestamp_ms > self.sweep_expiry_ms):
                next_state = MarketState.TRENDING
                confidence = 0.70

        elif self.current_state == MarketState.EXPANDING:
            # EXPANDING -> TRENDING when: imbalance > 2.5 sustained for 10s
            if signal.imbalance > self.trending_imbalance:
                if self.trending_candidate_start is None:
                    self.trending_candidate_start = timestamp
                elif timestamp - self.trending_candidate_start >= self.trending_duration_ms:
                    next_state = MarketState.TRENDING
                    self.trending_candidate_start = None
                    confidence = 0.85
            else:
                self.trending_candidate_start = None

        # ANY -> BALANCED when: imbalance < 1.2 AND velocity low for 60s
        if next_state != MarketState.BALANCED:
            if signal.imbalance < self.balanced_imbalance and not signal.velocity_expanding:
                if self.balanced_candidate_start is None:
                    self.balanced_candidate_start = timestamp
                elif timestamp - self.balanced_candidate_start >= self.balanced_duration_ms:
                    next_state = MarketState.BALANCED
                    self.balanced_candidate_start = None
                    confidence = 0.80
            else:
                self.balanced_candidate_start = None

        # Apply state transition
        if next_state != self.current_state:
            from_state = self.current_state.name
            to_state = next_state.name
            self.previous_state = self.current_state
            self.current_state = next_state
            self.state_start_time = timestamp
            self.state_duration_ms = 0
            self.state_probability = confidence
            
            transition = StateTransition(
                from_state=from_state,
                to_state=to_state,
                timestamp_ms=timestamp,
                confidence=confidence,
                signal_snapshot=signal
            )
            
            if self.debug:
                print(f"Regime transition detected: {from_state} -> {to_state} | Conf: {confidence:.2f}")
            return transition

        # Adjust state probability slightly based on duration / matching conditions
        self.state_probability = min(1.0, max(0.1, self.state_probability + 0.001))
        return None
