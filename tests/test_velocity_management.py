import pytest
from datetime import datetime, timezone, timedelta
from axonai.realtime.backtester import BacktestEngine
from axonai.realtime.live_state import PriceLevel
from axonai.realtime.event_types import LiveCandle, MarketEvent, EventType, EventPriority
from axonai.world_state import WorldState

@pytest.mark.unit
class TestVelocityManagement:
    def test_invalidation_sl_placement(self):
        # Setup engine
        engine = BacktestEngine(ticker="EURUSD=X", days=1)
        engine.live_state._state = WorldState(
            regime_scores={"trending": 0.3, "ranging": 0.7, "breakout": 0.0, "compression": 0.0, "panic": 0.0},
            dominant_regime="ranging",
            regime_confidence=0.7,
            volatility_regime="medium",
            atr_14_h1=0.0010,
            session="london",
            session_quality=0.8,
            session_penalty=1.0,
            hours_since_london_open=2.0,
            spread_pips=1.0,
            spread_safe=True,
            eur_strength=0.0,
            usd_strength=0.0,
            belief_score=0.8,
            should_run_graph=True,
            abort_reason=""
        )
        
        # Setup 3 completed M15 candles: lows 1.1510, 1.1500, 1.1520; highs 1.1530, 1.1540, 1.1535
        now = datetime.now(timezone.utc)
        engine.live_evidence._m15_candles.append(LiveCandle("M15", 1.1520, 1.1530, 1.1510, 1.1515, 100, now - timedelta(minutes=45), True))
        engine.live_evidence._m15_candles.append(LiveCandle("M15", 1.1515, 1.1540, 1.1500, 1.1535, 100, now - timedelta(minutes=30), True))
        engine.live_evidence._m15_candles.append(LiveCandle("M15", 1.1535, 1.1535, 1.1520, 1.1525, 100, now - timedelta(minutes=15), True))
        
        # Set support level
        engine.live_evidence.price_levels = [
            PriceLevel(1.1490, "SUPPORT", "H1", 0, now, "support", 0.8, True),
            PriceLevel(1.1560, "RESISTANCE", "H1", 0, now, "resistance", 0.8, True)
        ]
        
        # 1. Trigger BUY at 1.1520
        event_buy = MarketEvent(
            event_type=EventType.PEAK_DETECTION,
            priority=EventPriority.HIGH,
            timestamp=now,
            symbol="EURUSD=X",
            price=1.1520,
            details={"peak_type": "microstructure_exhaustion"}
        )
        
        # Mock Decision Layer
        engine.decision_layer.evaluate = lambda state, evidence, evt: {
            "trade_allowed": True,
            "direction": "BUY",
            "reason": "Exhaustion",
            "confidence": 0.8,
            "explainability": {}
        }
        
        engine._check_trade_triggers(event_buy)
        assert len(engine.active_trades) == 1
        buy_trade = engine.active_trades[0]
        
        # Expected Invalidation Low: min of recent lows (1.1500) and closest support (1.1490) -> 1.1490
        # Expected SL distance: (1.1520 - 1.1490) + 0.5 * ATR (0.0005) = 0.0035 (35 pips). Enforced cap: max 25 pips = 0.0025.
        # Expected SL = 1.1520 - 0.0025 = 1.1495
        # Expected TP = 1.1520 + 3 * SL_distance (0.0025) = 1.1595
        assert buy_trade["sl"] == pytest.approx(1.1495)
        assert buy_trade["tp"] == pytest.approx(1.1595)
        assert buy_trade["stage"] == 1
        assert buy_trade["peak_profit"] == 0.0
        
    def test_stage2_slowdown_trailing(self):
        engine = BacktestEngine(ticker="EURUSD=X", days=1)
        engine.live_state._state = WorldState(
            regime_scores={"trending": 0.3, "ranging": 0.7, "breakout": 0.0, "compression": 0.0, "panic": 0.0},
            dominant_regime="ranging",
            regime_confidence=0.7,
            volatility_regime="medium",
            atr_14_h1=0.0010,
            session="london",
            session_quality=0.8,
            session_penalty=1.0,
            hours_since_london_open=2.0,
            spread_pips=1.0,
            spread_safe=True,
            eur_strength=0.0,
            usd_strength=0.0,
            belief_score=0.8,
            should_run_graph=True,
            abort_reason=""
        )
        
        # Create an active BUY trade at entry 1.1500, SL at 1.1490, TP at 1.1530
        trade = {
            "id": 1,
            "direction": "BUY",
            "entry_time": datetime.now(),
            "entry_price": 1.1500,
            "sl": 1.1490,
            "tp": 1.1530,
            "trigger": "test",
            "status": "OPEN",
            "exit_time": None,
            "exit_price": None,
            "pips": 0.0,
            "close_reason": "",
            "peak_efficiency": 0.9,
            "peak_profit": 8.0,  # 8 pips peak profit
            "stage": 1
        }
        engine.active_trades.append(trade)
        engine.simulated_trades.append(trade)
        
        # Mock peak detector with low efficiency (0.4 < 0.6 * 0.9 = 0.54)
        engine.event_detector.peak_detector.last_efficiency = 0.4
        
        # Update simulated positions on a bid price of 1.1507 (7 pips profit)
        engine._update_simulated_positions(1.1507, 1.1508, datetime.now())
        
        # Trade should have transitioned to Stage 2 slowdown
        assert trade["stage"] == 2
        # Since peak profit 8.0 >= 5.0, new SL should lock 50% of peak profit: 
        # Entry (1.1500) + 4 pips (0.0004) = 1.1504
        assert trade["sl"] == pytest.approx(1.1504)
        
    def test_stage3_exhaustion_exit(self):
        engine = BacktestEngine(ticker="EURUSD=X", days=1)
        
        now = datetime.now()
        # Create active BUY trade
        trade = {
            "id": 2,
            "direction": "BUY",
            "entry_time": now,
            "entry_price": 1.1500,
            "sl": 1.1480,
            "tp": 1.1540,
            "trigger": "test",
            "status": "OPEN",
            "exit_time": None,
            "exit_price": None,
            "pips": 0.0,
            "close_reason": "",
            "peak_efficiency": 0.8,
            "peak_profit": 0.0,
            "stage": 1
        }
        engine.active_trades.append(trade)
        engine.simulated_trades.append(trade)
        
        pd = engine.event_detector.peak_detector
        
        # Populate history queues: 30 ticks of stalled prices (max occurred at start, i.e., index 0)
        # and opposing sell velocity significantly exceeding buy velocity
        pd.tick_prices.extend([1.1510] + [1.1505] * 29)
        pd.buy_velocities.extend([0.5] * 30)
        pd.sell_velocities.extend([4.0] * 30)  # average sell (4.0) > 1.5 * buy (0.5), and > 2.0
        pd.timestamps.extend([now + timedelta(seconds=i) for i in range(30)])
        
        # Call update
        engine._update_simulated_positions(1.1505, 1.1506, now + timedelta(seconds=35))
        
        # Trade should have been closed via Stage 3 Exhaustion Close
        assert len(engine.active_trades) == 0
        assert trade["status"] == "WIN"  # closed at 1.1505 (entry 1.1500) -> +5 pips
        assert "Exhaustion Exit" in trade["close_reason"]
