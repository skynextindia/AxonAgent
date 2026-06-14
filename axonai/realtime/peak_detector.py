"""Peak and climax exhaustion detection engine with advanced tick microstructure metrics."""

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class PeakSignal:
    peak_type: str
    direction: str
    peak_price: float
    intensity: str
    velocity_divergence: float
    price_per_tick_efficiency: float  
    divergence_warning: bool          # early signal, fires first
    peak_confirmed: bool              # Rule B — both conditions met
    peak_confidence: float            # tick-microstructure weighted score 0.0–1.0
    swing_confidence: Optional[float] = None  # swing-structure confidence (Rule C only)

    def to_dict(self) -> dict:
        d = {
            "peak_type": self.peak_type,
            "direction": self.direction,
            "peak_price": self.peak_price,
            "intensity": self.intensity,
            "velocity_divergence": self.velocity_divergence,
            "price_per_tick_efficiency": self.price_per_tick_efficiency,
            "divergence_warning": self.divergence_warning,
            "peak_confirmed": self.peak_confirmed,
            "peak_confidence": self.peak_confidence,
        }
        if self.swing_confidence is not None:
            d["swing_confidence"] = self.swing_confidence
        return d

class PeakDetector:
    """Detects price exhaustion peaks, volume climaxes, price-per-tick efficiency collapses, and velocity divergences."""

    def __init__(self, window_size: int = 1000, pip_mult: float = 0.0001, rule_c_enabled: bool = True,
                 use_pinpoint_price: bool = False, correct_rule_a_direction: bool = False,
                 cooldown_bypass_better_peak: bool = False):
        self.window_size = window_size
        self.pip_mult = pip_mult
        self.rule_c_enabled = rule_c_enabled
        self.use_pinpoint_price = use_pinpoint_price
        self.correct_rule_a_direction = correct_rule_a_direction
        self.cooldown_bypass_better_peak = cooldown_bypass_better_peak
        
        # History buffers (maxlen=window_size allows tracking enough ticks for 60s window)
        self.tick_prices: deque[float] = deque(maxlen=window_size)
        self.tick_velocities: deque[float] = deque(maxlen=window_size)
        self.timestamps: deque[datetime] = deque(maxlen=window_size)
        
        # Separate buy (up) and sell (down) velocities for acceleration & divergence analysis
        self.buy_velocities: deque[float] = deque(maxlen=window_size)
        self.sell_velocities: deque[float] = deque(maxlen=window_size)
        
        # Log-scaled divergence history for Z-score calculation
        self.log_divergences: deque[float] = deque(maxlen=window_size)
        
        # Volume history
        self.tick_volumes: deque[int] = deque(maxlen=window_size)
        
        # Track peaks to avoid double firing
        self.last_fired_peak_time: Optional[datetime] = None
        
        # Cooldown trackers (Audit Fix 2)
        self._last_confirmed_time: Optional[datetime] = None
        self._last_confirmed_price: float = 0.0

        # Latest raw metrics for active trade management
        self.last_buy_vel = 0.0
        self.last_sell_vel = 0.0
        self.last_dominant_side = "buy"
        self.last_efficiency = 1.0
        self.last_velocity_divergence = 0.0
        self.last_z_score = 0.0

    def update(self, price: float, timestamp: datetime, volume: int = 1) -> Optional[PeakSignal]:
        """Update indicators with new tick data and check for peaks.
        
        Returns:
            A PeakSignal containing peak details if a peak is detected, otherwise None.
        """
        self.tick_prices.append(price)
        self.timestamps.append(timestamp)
        self.tick_volumes.append(volume)

        # Compute tick-count velocity using a constant virtual time step of 0.05s per tick.
        # This completely decouples calculations from raw clock time, making it 100% broker-portable.
        velocity = 0.0
        buy_vel = 0.0
        sell_vel = 0.0
        
        if len(self.tick_prices) > 1:
            prev_price = self.tick_prices[-2]
            dt = 0.05  # constant virtual time step per event step
            
            dp = price - prev_price
            velocity = (abs(dp) / dt) / self.pip_mult
            
            if dp > 0:
                buy_vel = velocity
            elif dp < 0:
                sell_vel = velocity

        self.tick_velocities.append(velocity)
        self.buy_velocities.append(buy_vel)
        self.sell_velocities.append(sell_vel)

        if len(self.tick_prices) < 10:
            return None

        # 1. Use a fixed rolling window of 50 ticks (completely broker-portable)
        recent_indices = list(range(max(0, len(self.timestamps) - 50), len(self.timestamps)))

        recent_prices = [self.tick_prices[idx] for idx in recent_indices]
        recent_velocities = [self.tick_velocities[idx] for idx in recent_indices]
        recent_buy_vels = [self.buy_velocities[idx] for idx in recent_indices]
        recent_sell_vels = [self.sell_velocities[idx] for idx in recent_indices]

        # 2. Compute price-per-tick efficiency
        pip_movement = abs(recent_prices[-1] - recent_prices[0]) / self.pip_mult
        
        aggressive_ticks = 0
        for i in range(1, len(recent_prices)):
            if recent_prices[i] != recent_prices[i-1]:
                aggressive_ticks += 1
                
        price_per_tick_efficiency = pip_movement / max(1, aggressive_ticks)

        # 3. Compute velocity divergence with constant virtual time step dt = 0.05
        buy_acc = 0.0
        sell_acc = 0.0
        if len(recent_indices) > 1:
            dt = 0.05
            buy_acc = (recent_buy_vels[-1] - recent_buy_vels[-2]) / dt
            sell_acc = (recent_sell_vels[-1] - recent_sell_vels[-2]) / dt

        # Determine dominant trend direction over the active window
        dominant_buy = sum(1 for i in range(1, len(recent_prices)) if recent_prices[i] > recent_prices[i-1])
        dominant_sell = sum(1 for i in range(1, len(recent_prices)) if recent_prices[i] < recent_prices[i-1])
        
        if dominant_buy >= dominant_sell:
            velocity_divergence = sell_acc - buy_acc
            dominant_side = "buy"
            opposing_vel = recent_sell_vels[-1]
            dom_vel = recent_buy_vels[-1]
        else:
            velocity_divergence = buy_acc - sell_acc
            dominant_side = "sell"
            opposing_vel = recent_buy_vels[-1]
            dom_vel = recent_sell_vels[-1]

        # Logarithmic Scaling for Velocity Divergence
        import math
        log_divergence = math.log10(1.0 + abs(velocity_divergence)) if velocity_divergence > 0 else -math.log10(1.0 + abs(velocity_divergence))
        self.log_divergences.append(log_divergence)

        # Calculate Z-Score of Log Divergence
        z_score = 0.0
        if len(self.log_divergences) > 30:
            log_divs = list(self.log_divergences)
            mean_log_div = sum(log_divs) / len(log_divs)
            var_log_div = sum((x - mean_log_div) ** 2 for x in log_divs) / len(log_divs)
            std_log_div = var_log_div ** 0.5
            if std_log_div > 0:
                z_score = (log_divergence - mean_log_div) / std_log_div

        # Calculate Relative Volume
        relative_volume = 1.0
        if len(self.tick_volumes) > 30:
            avg_vol = sum(self.tick_volumes) / len(self.tick_volumes)
            if avg_vol > 0:
                relative_volume = volume / avg_vol

        # Update latest raw metrics
        self.last_buy_vel = buy_vel
        self.last_sell_vel = sell_vel
        self.last_dominant_side = dominant_side
        self.last_efficiency = price_per_tick_efficiency
        self.last_velocity_divergence = velocity_divergence
        self.last_z_score = z_score

        # Detect backtest-interpolated ticks (60s apart)
        is_backtest = False
        if len(self.timestamps) > 1:
            if (self.timestamps[-1] - self.timestamps[-2]).total_seconds() >= 5.0:
                is_backtest = True

        # Early-warning signals (dynamic backtest thresholding)
        # Using Z-Score for Dynamic Thresholds instead of hardcoded 0.6 / 0.8
        divergence_active = z_score > 2.0 or (is_backtest and velocity_divergence > 0.6)
        efficiency_threshold = 0.35 if is_backtest else 0.10
        efficiency_collapsed = price_per_tick_efficiency < efficiency_threshold
        spread_inverted = opposing_vel > dom_vel
        
        # standalone early-warning signal
        divergence_warning = z_score > 2.5 or (is_backtest and velocity_divergence > 0.8)
        
        # Rule B Confirmation: High divergence + Efficiency collapse OR massive relative volume spike
        peak_confirmed = (z_score > 2.5 or (is_backtest and velocity_divergence > 0.8)) and (efficiency_collapsed or relative_volume > 3.0)

        # Store for dashboard live telemetry
        self._last_divergence = log_divergence  # Use log_divergence for UI readability
        self._last_efficiency = price_per_tick_efficiency
        self._last_peak_confirmed = peak_confirmed

        # Determine pinpoint price (the absolute extreme of the move)
        if dominant_side == "sell":
            pinpoint_price = min(recent_prices)
        else:
            pinpoint_price = max(recent_prices)

        # Cooldown Suppression (tightened: longer time + wider price gap)
        COOLDOWN_SEC = 120.0
        COOLDOWN_PIPS = 3.0

        if self._last_confirmed_time is not None:
            elapsed = (timestamp - self._last_confirmed_time).total_seconds()
            p_val = pinpoint_price if self.use_pinpoint_price else price
            pip_distance = abs(p_val - self._last_confirmed_price) / self.pip_mult
            if elapsed < COOLDOWN_SEC and pip_distance < COOLDOWN_PIPS:
                # Bypass suppression if this is a better (more extreme) peak
                # For bullish reversal (dominant_side == "sell"), we want a lower price
                # For bearish reversal (dominant_side == "buy"), we want a higher price
                is_better_peak = False
                if self.cooldown_bypass_better_peak:
                    if dominant_side == "sell" and pinpoint_price < self._last_confirmed_price:
                        is_better_peak = True
                    elif dominant_side == "buy" and pinpoint_price > self._last_confirmed_price:
                        is_better_peak = True
                
                if not is_better_peak:
                    peak_confirmed = False

        # If peak is confirmed, update the tracking values
        if peak_confirmed:
            self._last_confirmed_time = timestamp
            self._last_confirmed_price = pinpoint_price if self.use_pinpoint_price else price

        # Weighted Confidence Score
        peak_confidence = (
            0.50 * (1.0 if divergence_active else 0.0) +
            0.35 * (1.0 if efficiency_collapsed else 0.0) +
            0.15 * (1.0 if spread_inverted else 0.0)
        )

        # 4. Microstructure Peak & Reversal Detection Rules
        # Rule A: Velocity exhaustion climax (requires at least divergence or efficiency collapse)
        # Split indices into baseline (older than 10s) and spike/climax (last 10s) windows
        baseline_indices = [
            idx for idx in recent_indices
            if (timestamp - self.timestamps[idx]).total_seconds() > 10.0
        ]
        spike_indices = [
            idx for idx in recent_indices
            if (timestamp - self.timestamps[idx]).total_seconds() <= 10.0
        ]
        
        # Fallback if baseline or spike is empty
        if not baseline_indices:
            split_pt = len(recent_indices) // 2
            baseline_indices = recent_indices[:split_pt]
            spike_indices = recent_indices[split_pt:]

        if len(baseline_indices) > 0 and len(spike_indices) > 0:
            baseline_vels = [self.tick_velocities[idx] for idx in baseline_indices]
            spike_vels = [self.tick_velocities[idx] for idx in spike_indices]
            
            max_vel = max(spike_vels)
            avg_vel = sum(baseline_vels) / len(baseline_vels) if len(baseline_vels) > 0 else 1.0
            
            # Store for dashboard live telemetry
            self._last_max_vel = max_vel
            self._last_avg_vel = avg_vel
            
            # Calculate dynamic velocity floor using rolling mean and standard deviation (Z-score = 3.0)
            all_vels = list(self.tick_velocities)
            if len(all_vels) >= 30:
                mean_vel = sum(all_vels) / len(all_vels)
                variance = sum((x - mean_vel) ** 2 for x in all_vels) / len(all_vels)
                std_vel = variance ** 0.5
                dynamic_floor = max(5.0, mean_vel + 3.0 * std_vel)
            else:
                dynamic_floor = 25.0  # Warm-up fallback
                
            if max_vel > 5.0 * avg_vel and max_vel > dynamic_floor:
                current_vel = recent_velocities[-1]
                if current_vel < 0.25 * max_vel and (divergence_active or efficiency_collapsed):
                    if self.last_fired_peak_time is None or (timestamp - self.last_fired_peak_time).total_seconds() > 180:
                        self.last_fired_peak_time = timestamp
                        max_vel_idx = spike_indices[spike_vels.index(max_vel)]
                        max_vel_price = self.tick_prices[max_vel_idx]
                        
                        if self.correct_rule_a_direction:
                            # Correct direction classification:
                            # - If dominant side was BUY (price spiked up), it's a bearish reversal (bearish_exhaustion)
                            # - If dominant side was SELL (price spiked down), it's a bullish reversal (bullish_exhaustion)
                            if dominant_side == "buy":
                                direction = "bearish_exhaustion"
                                pinpoint_a = max(recent_prices) if self.use_pinpoint_price else max_vel_price
                            else:
                                direction = "bullish_exhaustion"
                                pinpoint_a = min(recent_prices) if self.use_pinpoint_price else max_vel_price
                        else:
                            # Old direction logic:
                            direction = "bullish_exhaustion" if price < max_vel_price else "bearish_exhaustion"
                            pinpoint_a = max_vel_price
                        
                        logger.info("PeakDetector: Velocity exhaustion climax detected. Max velocity: %.2f", max_vel)
                        return PeakSignal(
                            peak_type="velocity_exhaustion",
                            direction=direction,
                            peak_price=pinpoint_a,
                            intensity="HIGH",
                            velocity_divergence=log_divergence,
                            price_per_tick_efficiency=price_per_tick_efficiency,
                            divergence_warning=divergence_warning,
                            peak_confirmed=peak_confirmed,
                            peak_confidence=peak_confidence
                        )

        # Rule B: Early-warning Reversal Trigger
        if peak_confirmed:
            if self.last_fired_peak_time is None or (timestamp - self.last_fired_peak_time).total_seconds() > 60:
                self.last_fired_peak_time = timestamp
                direction = "bullish_reversal" if dominant_side == "sell" else "bearish_reversal"
                
                logger.info("PeakDetector: Advanced microstructure peak detected! Efficiency: %.4f, Log Divergence: %.2f (Z: %.2f)", 
                            price_per_tick_efficiency, log_divergence, z_score)
                return PeakSignal(
                    peak_type="microstructure_exhaustion",
                    direction=direction,
                    peak_price=pinpoint_price if self.use_pinpoint_price else price,
                    intensity="HIGH",
                    velocity_divergence=log_divergence,
                    price_per_tick_efficiency=price_per_tick_efficiency,
                    divergence_warning=divergence_warning,
                    peak_confirmed=peak_confirmed,
                    peak_confidence=peak_confidence
                )

        # Rule C: Fractal local swing peak fallback (wider window + longer cooldown to reduce noise)
        # Disabled by default — generates excessive noise from tick interpolation artifacts
        if not self.rule_c_enabled:
            return None
        prices = list(self.tick_prices)
        if len(prices) >= 11:
            mid_idx = -6
            mid_val = prices[mid_idx]
            left_side = prices[-11:mid_idx]
            right_side = prices[mid_idx+1:]
            
            # Require minimum swing amplitude of 1.5 pips to filter micro-noise
            swing_amplitude_high = mid_val - min(min(left_side), min(right_side))
            swing_amplitude_low = max(max(left_side), max(right_side)) - mid_val
            
            if mid_val > max(left_side) and mid_val > max(right_side):
                if swing_amplitude_high >= 1.5 * self.pip_mult:  # min 1.5 pip swing
                    if self.last_fired_peak_time is None or (timestamp - self.last_fired_peak_time).total_seconds() > 300:
                        self.last_fired_peak_time = timestamp
                        # Swing-structure confidence (separate from tick-based peak_confidence)
                        amp_ratio = swing_amplitude_high / (1.5 * self.pip_mult)
                        retrace_pct = (mid_val - min(right_side)) / swing_amplitude_high
                        sc = min(0.3 + 0.4 * min(amp_ratio - 1.0, 1.0) + 0.3 * retrace_pct, 1.0)
                        return PeakSignal(
                            peak_type="local_swing_high",
                            direction="bearish_reversal",
                            peak_price=mid_val,
                            intensity="MEDIUM",
                            velocity_divergence=log_divergence,
                            price_per_tick_efficiency=price_per_tick_efficiency,
                            divergence_warning=divergence_warning,
                            peak_confirmed=peak_confirmed,
                            peak_confidence=peak_confidence,
                            swing_confidence=sc
                        )
                    
            if mid_val < min(left_side) and mid_val < min(right_side):
                if swing_amplitude_low >= 1.5 * self.pip_mult:  # min 1.5 pip swing
                    if self.last_fired_peak_time is None or (timestamp - self.last_fired_peak_time).total_seconds() > 300:
                        self.last_fired_peak_time = timestamp
                        # Swing-structure confidence (separate from tick-based peak_confidence)
                        amp_ratio = swing_amplitude_low / (1.5 * self.pip_mult)
                        retrace_pct = (max(right_side) - mid_val) / swing_amplitude_low
                        sc = min(0.3 + 0.4 * min(amp_ratio - 1.0, 1.0) + 0.3 * retrace_pct, 1.0)
                        return PeakSignal(
                            peak_type="local_swing_low",
                            direction="bullish_reversal",
                            peak_price=mid_val,
                            intensity="MEDIUM",
                            velocity_divergence=log_divergence,
                            price_per_tick_efficiency=price_per_tick_efficiency,
                            divergence_warning=divergence_warning,
                            peak_confirmed=peak_confirmed,
                            peak_confidence=peak_confidence,
                            swing_confidence=sc
                        )

        return None
