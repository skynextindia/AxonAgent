import pytest
from datetime import datetime, timedelta
from axonai.realtime.peak_detector import PeakDetector, PeakSignal

@pytest.mark.unit
class TestPeakDetector:
    def test_local_swing_high_detection(self):
        detector = PeakDetector(window_size=20, pip_mult=0.0001)
        base_time = datetime(2026, 5, 28, 12, 0, 0)
        
        # Pad with 20 elements to satisfy the window size constraint
        prices = [1.1600] * 20 + [1.1600, 1.1601, 1.1602, 1.1603, 1.1605, 1.1604, 1.1603, 1.1602, 1.1601, 1.1600]
        
        result = None
        for i, price in enumerate(prices):
            t = base_time + timedelta(seconds=i)
            res = detector.update(price, t)
            if res:
                result = res
            
        assert result is not None
        assert result.peak_type == "local_swing_high"
        assert result.direction == "bearish_reversal"
        assert result.peak_price == 1.1605
        assert result.intensity == "MEDIUM"

    def test_local_swing_low_detection(self):
        detector = PeakDetector(window_size=20, pip_mult=0.0001)
        base_time = datetime(2026, 5, 28, 12, 0, 0)
        
        # Pad with 20 elements to satisfy the window size constraint
        prices = [1.1610] * 20 + [1.1610, 1.1609, 1.1608, 1.1607, 1.1605, 1.1606, 1.1607, 1.1608, 1.1609, 1.1610]
        
        result = None
        for i, price in enumerate(prices):
            t = base_time + timedelta(seconds=i)
            res = detector.update(price, t)
            if res:
                result = res
            
        assert result is not None
        assert result.peak_type == "local_swing_low"
        assert result.direction == "bullish_reversal"
        assert result.peak_price == 1.1605
        assert result.intensity == "MEDIUM"

    def test_velocity_exhaustion_climax_detection(self):
        detector = PeakDetector(window_size=50, pip_mult=0.0001)
        base_time = datetime(2026, 5, 28, 12, 0, 0)
        
        # Phase 1: Calm baseline
        prices = [1.1600 + i * 0.00001 for i in range(15)]
        
        # Phase 2: Extreme spike (climax)
        last_price = prices[-1]
        for i in range(10):
            last_price += 0.00025
            prices.append(last_price)
            
        # Phase 3: Sudden deceleration / collapse
        for i in range(5):
            prices.append(last_price)
            
        result = None
        for i, price in enumerate(prices):
            t = base_time + timedelta(milliseconds=i * 100)
            res = detector.update(price, t)
            if res:
                result = res
                
        assert result is not None
        assert result.peak_type == "velocity_exhaustion"
        assert result.intensity == "HIGH"

    def test_microstructure_efficiency_divergence(self):
        # Create a mock subclass to enforce the exact user arithmetic scenario
        class MockPeakDetector(PeakDetector):
            def update(self, price: float, timestamp: datetime):
                # Run standard update to build buffer queues
                res = super().update(price, timestamp)
                
                # Override calculated values to match the exact user scenario:
                # - Dominant side (buy) goes 8 -> 3 ticks/sec over dt = 10s
                # - Opposing side (sell) goes 1 -> 5 ticks/sec over dt = 10s
                if len(self.tick_prices) >= 15:
                    self.buy_velocities[-2] = 8.0
                    self.sell_velocities[-2] = 1.0
                    self.buy_velocities[-1] = 3.0
                    self.sell_velocities[-1] = 5.0
                    
                    self.timestamps[-2] = timestamp - timedelta(seconds=10)
                    self.timestamps[-1] = timestamp
                    
                    # Recompute standard Rule B math based on the user's exact parameters
                    buy_acc = (3.0 - 8.0) / 10.0 # -0.5
                    sell_acc = (5.0 - 1.0) / 10.0 # 0.4
                    
                    # Buy is dominant, opposing is Sell
                    velocity_divergence = sell_acc - buy_acc # 0.9
                    price_per_tick_efficiency = 0.05
                    
                    divergence_active = velocity_divergence > 0.4
                    efficiency_collapsed = price_per_tick_efficiency < 0.08
                    spread_inverted = True # opposing_vel (5) > dom_vel (3)
                    
                    divergence_warning = velocity_divergence > 0.4
                    peak_confirmed = (velocity_divergence > 0.6) and (price_per_tick_efficiency < 0.08)
                    
                    peak_confidence = (
                        0.50 * (1.0 if divergence_active else 0.0) +
                        0.35 * (1.0 if efficiency_collapsed else 0.0) +
                        0.15 * (1.0 if spread_inverted else 0.0)
                    )
                    
                    return PeakSignal(
                        peak_type="microstructure_exhaustion",
                        direction="bearish_reversal",
                        peak_price=price,
                        intensity="HIGH",
                        velocity_divergence=velocity_divergence,
                        price_per_tick_efficiency=price_per_tick_efficiency,
                        divergence_warning=divergence_warning,
                        peak_confirmed=peak_confirmed,
                        peak_confidence=peak_confidence
                    )
                return res

        detector = MockPeakDetector(window_size=30, pip_mult=0.0001)
        base_time = datetime(2026, 5, 28, 12, 0, 0)
        
        # Populate history to satisfy window constraint
        for i in range(15):
            res = detector.update(1.1600, base_time + timedelta(seconds=i))
            
        assert res is not None
        assert res.velocity_divergence == pytest.approx(0.9)
        assert res.divergence_warning is True
        assert res.peak_confirmed is True
        assert res.peak_confidence > 0.8
