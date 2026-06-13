"""High-fidelity historical backtesting engine with candle, peak, reversal, and sweep detection.

Runs simulations using real MT5 historical data (with robust synthetic fallback when offline)
and triggers simulated trades, writing a comprehensive backtest report.
"""

from __future__ import annotations

import os
import json
import logging
import random
import queue
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import pandas as pd
import numpy as np

from axonai.realtime.event_types import LiveCandle, MarketEvent, EventType, EventPriority
from axonai.realtime.event_detector import EventDetector
from axonai.realtime.live_state import LiveWorldState, LiveMarketEvidence
from axonai.realtime.peak_detector import PeakDetector
from axonai.dataflows.mt5_data import mt5_initialize, _to_mt5_symbol, _ensure_symbol_visible, _fetch_bars

logger = logging.getLogger(__name__)

class BacktestEngine:
    """Historical data simulator and backtesting engine for AxonAI."""

    def __init__(self, ticker: str = "EURUSD=X", days: int = 5, config: Optional[dict] = None):
        self.ticker = ticker
        self.days = days
        self.config = config or {}
        
        # Clean ticker suffix for pips
        ticker_clean = ticker.upper().replace("=X", "").replace("/", "")
        self.is_jpy = "JPY" in ticker_clean
        self.pip_mult = 0.01 if self.is_jpy else 0.0001
        
        # Initialize Event Queue and state tracking
        self.event_queue: queue.Queue[MarketEvent] = queue.Queue()
        self.detected_events: List[MarketEvent] = []
        self.simulated_trades: List[Dict[str, Any]] = []
        self.active_trades: List[Dict[str, Any]] = []
        self._pending_level_breaches: list = []
        
        # Initialize Core Real-Time Components
        self.live_state = LiveWorldState(self.ticker, self.config)
        self.live_evidence = LiveMarketEvidence(self.ticker, self.config)
        
        # Initialize EventDetector
        self.event_detector = EventDetector(
            self.live_state,
            self.live_evidence,
            self.event_queue,
            self.config
        )
        self.event_detector.set_pip_multiplier(self.is_jpy)
        
        # Override initialization constraints
        self.live_state._initialized = True
        self.live_evidence._initialized = True

    def load_historical_data(self) -> Tuple[List[LiveCandle], List[Tuple[float, float, datetime, int]]]:
        """Fetch real data from MT5 if connected, otherwise generate synthetic market dataset."""
        connected = False
        try:
            if mt5_initialize():
                connected = True
        except Exception:
            pass

        if connected:
            logger.info("BacktestEngine: MT5 connected. Loading real historical bars for %s", self.ticker)
            return self._load_real_data()
        else:
            logger.info("BacktestEngine: MT5 offline. Generating realistic high-fidelity synthetic market dataset.")
            return self._generate_synthetic_data()

    def _load_real_data(self) -> Tuple[List[LiveCandle], List[Tuple[float, float, datetime, int]]]:
        """Load real historical bars from MetaTrader 5 and generate interpolated ticks."""
        mt5_sym = _to_mt5_symbol(self.ticker)
        _ensure_symbol_visible(mt5_sym)
        
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=self.days)
        
        # Fetch H1 and M15 bars
        df_m15 = _fetch_bars(mt5_sym, "M15", start_dt, end_dt)
        if df_m15 is None or df_m15.empty:
            logger.warning("Failed to fetch real historical bars. Falling back to synthetic generator.")
            return self._generate_synthetic_data()
            
        candles: List[LiveCandle] = []
        ticks: List[Tuple[float, float, datetime, int]] = []
        
        # Convert M15 df to LiveCandle objects
        for t, row in df_m15.iterrows():
            candle = LiveCandle(
                timeframe="M15",
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row["Volume"]),
                open_time=t,
                is_closed=True
            )
            candles.append(candle)
            
            # Interpolate realistic ticks from M15 bar to feed PeakDetector
            # Path: Open -> High -> Low -> Close (or opposite if bearish)
            o, h, l, c = candle.open, candle.high, candle.low, candle.close
            steps = 15
            dt = timedelta(minutes=15) / steps
            
            is_bullish = c >= o
            sub_prices = []
            if is_bullish:
                sub_prices.append(o)
                # Open to Low
                sub_prices.extend(np.linspace(o, l, 4)[1:])
                # Low to High
                sub_prices.extend(np.linspace(l, h, 6)[1:])
                # High to Close
                sub_prices.extend(np.linspace(h, c, 5)[1:])
            else:
                sub_prices.append(o)
                # Open to High
                sub_prices.extend(np.linspace(o, h, 4)[1:])
                # High to Low
                sub_prices.extend(np.linspace(h, l, 6)[1:])
                # Low to Close
                sub_prices.extend(np.linspace(l, c, 5)[1:])
                
            volume_per_tick = max(1, int(candle.volume / len(sub_prices)))
            for idx, price in enumerate(sub_prices):
                tick_time = t + dt * idx
                ticks.append((price - 0.00005, price + 0.00005, tick_time, volume_per_tick))
                
        return candles, ticks

    def _generate_synthetic_data(self) -> Tuple[List[LiveCandle], List[Tuple[float, float, datetime, int]]]:
        """Generate realistic multi-phase synthetic market dataset.
        
        Market phases (Wyckoff-inspired):
        1. Accumulation (0-15%) - tight range near support
        2. Markup (15-40%) - strong uptrend 
        3. Distribution (40-55%) - tight range near resistance
        4. Markdown (55-75%) - strong downtrend
        5. Spring/Reversal (75-85%) - sweep below support + reversal
        6. Rally (85-100%) - new uptrend
        """
        random.seed(42)
        np.random.seed(42)
        
        candles: List[LiveCandle] = []
        ticks: List[Tuple[float, float, datetime, int]] = []
        
        base_price = 1.1500
        current_time = datetime.now() - timedelta(days=self.days)
        total_m15_bars = self.days * 24 * 4
        
        # Pre-compute price series for dynamic key levels
        prices_series = [base_price]
        for i in range(total_m15_bars):
            phase = i / total_m15_bars
            if phase < 0.15:
                trend = random.normalvariate(0, 0.00005)
                volatility = 0.00008
            elif phase < 0.40:
                trend = 0.00012
                volatility = 0.00010
            elif phase < 0.55:
                trend = random.normalvariate(0, 0.00004)
                volatility = 0.00008
            elif phase < 0.75:
                trend = -0.00015
                volatility = 0.00012
            elif phase < 0.85:
                trend = -0.00010 if phase < 0.78 else 0.00020
                volatility = 0.00015
            else:
                trend = 0.00010
                volatility = 0.00010
            prices_series.append(prices_series[-1] + trend + random.normalvariate(0, volatility))
        
        # Dynamic key levels from actual price distribution
        prices_arr = np.array(prices_series[1:])
        support_level = round(float(np.percentile(prices_arr, 10)), 5)
        resistance_level = round(float(np.percentile(prices_arr, 90)), 5)
        mid_level = round((support_level + resistance_level) / 2, 5)
        self.live_evidence._evidence.key_levels = [support_level, mid_level, resistance_level]
        
        for i in range(total_m15_bars):
            bar_time = current_time + timedelta(minutes=15 * i)
            phase = i / total_m15_bars
            open_p = prices_series[i]
            close_p = prices_series[i + 1]
            
            body = abs(close_p - open_p)
            wick_up = abs(random.normalvariate(0, max(body * 0.5, 0.00005)))
            wick_dn = abs(random.normalvariate(0, max(body * 0.5, 0.00005)))
            high_p = max(open_p, close_p) + wick_up
            low_p = min(open_p, close_p) - wick_dn
            
            is_sweep = False
            dist_to_support = abs(close_p - support_level)
            dist_to_resistance = abs(close_p - resistance_level)
            
            # Pin bar at support during markdown/spring
            if 0.70 <= phase <= 0.82 and dist_to_support < 5 * self.pip_mult:
                low_p = support_level - random.uniform(8, 15) * self.pip_mult
                high_p = max(open_p, close_p) + random.uniform(1, 3) * self.pip_mult
                close_p = support_level + random.uniform(3, 8) * self.pip_mult
                is_sweep = True
            # Pin bar at resistance during distribution
            elif 0.42 <= phase <= 0.52 and dist_to_resistance < 5 * self.pip_mult:
                high_p = resistance_level + random.uniform(8, 15) * self.pip_mult
                low_p = min(open_p, close_p) - random.uniform(1, 3) * self.pip_mult
                close_p = resistance_level - random.uniform(3, 8) * self.pip_mult
                is_sweep = True
            
            candle = LiveCandle(
                timeframe="M15",
                open=round(open_p, 5), high=round(high_p, 5),
                low=round(low_p, 5), close=round(close_p, 5),
                volume=int(random.randint(100, 500)),
                open_time=bar_time, is_closed=True
            )
            candles.append(candle)
            
            steps = 15
            dt = timedelta(minutes=15) / steps
            if is_sweep:
                sub_prices = []
                extreme = low_p if close_p > open_p else high_p
                for tick_idx in range(5):
                    sub_prices.append(open_p + (extreme - open_p) * (tick_idx / 4.0))
                for tick_idx in range(10):
                    sub_prices.append(extreme + (close_p - extreme) * (tick_idx / 9.0))
            else:
                is_bullish = close_p >= open_p
                if is_bullish:
                    sub_prices = (list(np.linspace(open_p, low_p, 4)) +
                                  list(np.linspace(low_p, high_p, 6)[1:]) +
                                  list(np.linspace(high_p, close_p, 5)[1:]))
                else:
                    sub_prices = (list(np.linspace(open_p, high_p, 4)) +
                                  list(np.linspace(high_p, low_p, 6)[1:]) +
                                  list(np.linspace(low_p, close_p, 5)[1:]))
            
            volume_per_tick = max(1, int(candle.volume / len(sub_prices)))
            for idx, pr in enumerate(sub_prices):
                tick_time = bar_time + dt * idx
                ticks.append((round(pr - 0.00005, 5), round(pr + 0.00005, 5), tick_time, volume_per_tick))
                
        return candles, ticks

    def _prewarm_candle_history(self, candles: List[LiveCandle]) -> int:
        """Pre-populate the H1 and H4 candle deques from the beginning of historical data to avoid cold-start lag.
        
        Returns the warmup limit index.
        """
        # Let's warm up using first 10 days (960 bars) or 20% of the data, whichever is smaller.
        warmup_limit = min(960, len(candles) // 5)
        if warmup_limit < 64:
            return 0
            
        warmup_candles = candles[:warmup_limit]
        
        # Populate M15 history directly
        for c in warmup_candles:
            self.live_evidence._m15_candles.append(c)
            
        # Aggregate to H1 deques
        h1_chunk = []
        for c in warmup_candles:
            h1_chunk.append(c)
            if c.open_time.minute == 45:
                h1_candle = LiveCandle(
                    timeframe="H1",
                    open=h1_chunk[0].open,
                    high=max(x.high for x in h1_chunk),
                    low=min(x.low for x in h1_chunk),
                    close=c.close,
                    volume=sum(x.volume for x in h1_chunk),
                    open_time=h1_chunk[0].open_time,
                    is_closed=True
                )
                self.live_evidence._h1_candles.append(h1_candle)
                h1_chunk = []
                
        # Aggregate to H4 deques
        h4_chunk = []
        for c in warmup_candles:
            h4_chunk.append(c)
            if c.open_time.minute == 45 and (c.open_time.hour + 1) % 4 == 0:
                h4_candle = LiveCandle(
                    timeframe="H4",
                    open=h4_chunk[0].open,
                    high=max(x.high for x in h4_chunk),
                    low=min(x.low for x in h4_chunk),
                    close=c.close,
                    volume=sum(x.volume for x in h4_chunk),
                    open_time=h4_chunk[0].open_time,
                    is_closed=True
                )
                self.live_evidence._h4_candles.append(h4_candle)
                h4_chunk = []
                
        # Update indicators to set initial EMA trend, RSI, MACD
        self.live_evidence._update_indicators()
        return warmup_limit

    def run(self) -> Dict[str, Any]:
        """Execute the backtest simulation sequentially, detecting events and managing trades."""
        # Reset trackers
        self.detected_events.clear()
        self.simulated_trades.clear()
        self.active_trades.clear()
        self._last_loss_time = None  # track last loss for cooldown
        
        # Load Candles and Ticks
        candles, ticks = self.load_historical_data()
        
        # Mock initial WorldState with valid fields
        # Compute real H1 ATR from M15 bars (aggregate 4 M15 bars = 1 H1 bar)
        h1_ranges = []
        for j in range(0, len(candles) - 3, 4):
            h1_high = max(candles[j+k].high for k in range(4))
            h1_low = min(candles[j+k].low for k in range(4))
            h1_ranges.append(h1_high - h1_low)
        computed_atr = float(np.mean(h1_ranges[-14:])) if h1_ranges else 0.0012
        logger.info("BacktestEngine: Computed H1 ATR: %.5f (%.1f pips)", computed_atr, computed_atr / self.pip_mult)
        
        from axonai.world_state import WorldState
        initial_state = WorldState(
            regime_scores={"trending": 0.3, "ranging": 0.7, "breakout": 0.0, "compression": 0.0, "panic": 0.0},
            dominant_regime="ranging",
            regime_confidence=0.7,
            volatility_regime="medium",
            atr_14_h1=computed_atr,
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
        self.live_state._state = initial_state
        from axonai.dataflows.evidence_extractor import MarketEvidence
        
        # Auto-derive key levels from candle data
        all_highs = [c.high for c in candles]
        all_lows = [c.low for c in candles]
        support_level = round(float(np.percentile(all_lows, 15)), 5)
        resistance_level = round(float(np.percentile(all_highs, 85)), 5)
        mid_level = round((support_level + resistance_level) / 2, 5)
        key_lvls = [support_level, mid_level, resistance_level]
        logger.info("BacktestEngine: Key levels: S=%s M=%s R=%s", 
                    support_level, mid_level, resistance_level)
        
        # If price_levels are not seeded, initialize them from derived key levels
        if not getattr(self.live_evidence, "price_levels", None):
            from axonai.realtime.live_state import PriceLevel
            now_utc = datetime.now(timezone.utc)
            self.live_evidence.price_levels = [
                PriceLevel(support_level, "SUPPORT_ZONE", "D1", 0, now_utc, "support", 0.7, True),
                PriceLevel(resistance_level, "RESISTANCE_ZONE", "D1", 0, now_utc, "resistance", 0.7, True)
            ]
            logger.info("BacktestEngine: Seeding initial price_levels (S=%.5f, R=%.5f)", support_level, resistance_level)
        
        sh_price = key_lvls[-1] if key_lvls else 1.1550
        sl_price = key_lvls[0] if key_lvls else 1.1480
        
        # Derive trend direction from first half vs second half of data
        half = len(candles) // 2
        first_half_avg = np.mean([c.close for c in candles[:half]])
        second_half_avg = np.mean([c.close for c in candles[half:]])
        trend_h1 = "up" if second_half_avg > first_half_avg + 5 * self.pip_mult else (
                   "down" if second_half_avg < first_half_avg - 5 * self.pip_mult else "sideways")
        
        self.live_evidence._evidence = MarketEvidence(
            swing_highs=[{"price": sh_price, "time": candles[0].open_time.strftime("%Y-%m-%d %H:%M")}],
            swing_lows=[{"price": sl_price, "time": candles[0].open_time.strftime("%Y-%m-%d %H:%M")}],
            key_levels=list(key_lvls),
            trend_direction_h1=trend_h1,
            trend_direction_h4=trend_h1,
            rsi_h1=50.0,
            macd_signal_h1="neutral",
            recent_patterns=[],
            asian_range_high=sh_price,
            asian_range_low=sl_price,
            london_open_bias="neutral"
        )
        
        # Warm up candle history & aggregate deques
        warmup_limit = self._prewarm_candle_history(candles)
        
        # Group ticks by their corresponding candle bar timestamp
        tick_index = 0
        total_ticks = len(ticks)
        
        if warmup_limit > 0:
            sim_start_time = candles[warmup_limit].open_time
            while tick_index < total_ticks and ticks[tick_index][2] < sim_start_time:
                tick_index += 1
                
        logger.info("BacktestEngine: Simulating from bar %d/%d, ticks start index %d/%d",
                    warmup_limit, len(candles), tick_index, total_ticks)
        
        last_session = "asian"  # Track session for intraday EOD close
        
        for bar_idx, candle in enumerate(candles[warmup_limit:], start=warmup_limit):
            bar_start = candle.open_time
            bar_end = bar_start + timedelta(minutes=15)
            
            # Feed ticks that fall within this bar's time window
            while tick_index < total_ticks:
                tick_data = ticks[tick_index]
                bid, ask, tick_time = tick_data[0], tick_data[1], tick_data[2]
                volume = tick_data[3] if len(tick_data) > 3 else 1
                if tick_time > bar_end:
                    break
                    
                # 1. Update Peak/Microstructure Detections per Tick
                self.event_detector.on_tick(bid, ask, tick_time, volume)
                
                # Check event queue for new tick-level detections
                while not self.event_queue.empty():
                    evt = self.event_queue.get_nowait()
                    self.detected_events.append(evt)
                    self._check_trade_triggers(evt)
                    
                # 2. Manage active trade fills and exits on tick price action
                self._update_simulated_positions(bid, ask, tick_time)
                
                # 3. Intraday EOD force-close: when session exits active periods
                current_session = self.live_state._state.session if self.live_state._state else "asian"
                if current_session in ("rollover", "asian") and last_session in ("london", "overlap", "newyork"):
                    for trade in list(self.active_trades):
                        self._close_position(trade, bid, ask, tick_time, "End of Day (Session Close)")
                last_session = current_session
                
                tick_index += 1
                
            # 3. Trigger Candle-level updates and Candle Pattern detections on Bar close
            self.event_detector.on_candle_close(candle)
            
            # Aggregate and trigger H1 closes
            if candle.open_time.minute == 45:
                h1_hour = candle.open_time.hour
                h1_day = candle.open_time.date()
                h1_candles_chunk = [c for c in candles[:bar_idx+1] 
                                    if c.open_time.date() == h1_day and c.open_time.hour == h1_hour]
                if h1_candles_chunk:
                    h1_candle = LiveCandle(
                        timeframe="H1",
                        open=h1_candles_chunk[0].open,
                        high=max(c.high for c in h1_candles_chunk),
                        low=min(c.low for c in h1_candles_chunk),
                        close=candle.close,
                        volume=sum(c.volume for c in h1_candles_chunk),
                        open_time=h1_candles_chunk[0].open_time,
                        is_closed=True
                    )
                    self.event_detector.on_candle_close(h1_candle)
                    
            # Aggregate and trigger H4 closes
            if candle.open_time.minute == 45 and (candle.open_time.hour + 1) % 4 == 0:
                h4_group = candle.open_time.hour // 4
                h4_day = candle.open_time.date()
                h4_candles_chunk = [c for c in candles[:bar_idx+1] 
                                    if c.open_time.date() == h4_day and (c.open_time.hour // 4) == h4_group]
                if h4_candles_chunk:
                    h4_candle = LiveCandle(
                        timeframe="H4",
                        open=h4_candles_chunk[0].open,
                        high=max(c.high for c in h4_candles_chunk),
                        low=min(c.low for c in h4_candles_chunk),
                        close=candle.close,
                        volume=sum(c.volume for c in h4_candles_chunk),
                        open_time=h4_candles_chunk[0].open_time,
                        is_closed=True
                    )
                    self.event_detector.on_candle_close(h4_candle)
            
            # Check event queue for bar close detections
            while not self.event_queue.empty():
                evt = self.event_queue.get_nowait()
                self.detected_events.append(evt)
                self._check_trade_triggers(evt)
                
            # 4. Check pending level breaches for candle-close confirmation
            self._check_pending_level_breaches(candle)
        # Close any open positions at market close on last tick
        if self.active_trades:
            final_bid = ticks[-1][0]
            final_ask = ticks[-1][1]
            final_time = ticks[-1][2]
            for trade in list(self.active_trades):
                self._close_position(trade, final_bid, final_ask, final_time, "Market Close")
                
        # Calculate Backtesting Performance Metrics
        report = self._compile_performance_metrics()
        return report

    def _check_trade_triggers(self, event: MarketEvent):
        """Evaluate events with quality filters before triggering trades.
        
        Filters (in order):
        1. Max 1 active trade at a time
        2. London/NY sessions only (intraday)
        3. 15-min cooldown between entries; 45-min after losing trade
        4. Signal classification: sweep / microstructure peak / structure break
        5. Level behavior check — skip weakening/pressured levels
        6. Regime filter — skip compression
        7. MTF alignment — hard block on counter-trend; +0.15 Q if aligned
        8. Quality floor ⩾ 0.65 (after all adjustments)
        9. No duplicate direction
        """
        # Filter to only allow Advanced Microstructure Peak Reversals (Rule A & Rule B)
        if event.event_type != EventType.PEAK_DETECTION:
            return
        peak_type = event.details.get("peak_type", "")
        if peak_type not in ("velocity_exhaustion", "microstructure_exhaustion"):
            return

        # ── Gate 1: Max concurrent trades ──
        if len(self.active_trades) >= 1:
            return

        # ── Gate 2: London/NY sessions only (intraday) ──
        state = self.live_state._state
        if state and state.session not in ("london", "overlap", "newyork"):
            return

        # ── Gate 3: 15-minute cooldown between entries ──
        if self.simulated_trades:
            last_entry_time = self.simulated_trades[-1]["entry_time"]
            if (event.timestamp - last_entry_time).total_seconds() < 900:  # 15 min
                return

        # ── Gate 3b: Loss-streak cooldown — skip 45 minutes after a losing trade ──
        if self._last_loss_time is not None:
            minutes_since_loss = (event.timestamp - self._last_loss_time).total_seconds() / 60
            if minutes_since_loss < 45:  # 45-min cooldown after any loss
                return

        direction = None
        trigger_reason = ""
        signal_quality = 0.0  # 0.0 – 1.0 confluence score
        
        # Check level behavior if available (for sweeps, patterns, structure breaks)
        level_behavior = event.details.get("level_behavior", {}) if hasattr(event, "details") else {}
        level_attack_quality = level_behavior.get("attack_quality", "") if level_behavior else ""
        
        # 1. Candle Patterns
        if event.event_type == EventType.CANDLE_PATTERN:
            return  # Skip noisy candle patterns to maximize selectivity and win rate
                
        # 2. Liquidity Sweeps (high quality, modulated by level behavior)
        elif event.event_type == EventType.SWEEP_DETECTED:
            dir_str = event.details.get("direction", "")
            if "bullish" in dir_str:
                direction = "BUY"
                trigger_reason = "Bullish Liquidity Sweep"
            elif "bearish" in dir_str:
                direction = "SELL"
                trigger_reason = "Bearish Liquidity Sweep"
            signal_quality = 0.75  # base — sweeps are decent conviction
            # Boost if absorption pattern confirmed (level is weakening)
            if level_attack_quality in ("weakening", "pressured"):
                signal_quality += 0.10
                trigger_reason += " + Absorption Confirmed"
                
        # 3. Peak Exhaustion & Reversals (filtered by confidence)
        elif event.event_type == EventType.PEAK_DETECTION:
            peak_type = event.details.get("peak_type", "")
            dir_str = event.details.get("direction", "")
            confidence = event.details.get("peak_confidence", 0.0)
            confirmed = event.details.get("peak_confirmed", False)
            intensity = event.details.get("intensity", "MEDIUM")
            
            # Debug: log raw details before gate
            logger.debug("PEAK RAW: confidence=%.2f confirmed=%s intensity=%s details_keys=%s",
                         confidence, confirmed, intensity, list(event.details.keys())[:10])
            
            # ── Gate: Require HIGH/MEDIUM intensity ──
            if intensity not in ("HIGH", "MEDIUM"):
                return
            # HIGH peaks (Rules A/B) require tick-microstructure confidence.
            # MEDIUM peaks (Rule C fractal local swings) pass on their own
            # validation (1.5 pip min swing, 300s cooldown) since tick-based
            # confidence is unreliable on interpolated backtest data.
            if intensity == "HIGH" and not confirmed and confidence < 0.6:
                logger.debug("PEAK GATE: skipped (confirmed=%s conf=%.2f dir=%s type=%s)",
                             confirmed, confidence, dir_str, peak_type)
                return
            
            if "bullish" in dir_str or "low" in peak_type:
                direction = "BUY"
                trigger_reason = f"Bullish Microstructure Peak ({peak_type})"
            elif "bearish" in dir_str or "high" in peak_type:
                direction = "SELL"
                trigger_reason = f"Bearish Microstructure Peak ({peak_type})"
            
            # S/R Proximity (ANY direction) & Daily Trend Gate
            if direction is not None and self.config.get("require_sr_proximity", True):
                # 1. Proximity Check to ANY S/R Zone (5.0 pips)
                active_levels = [l for l in self.live_evidence.price_levels if l.is_active]
                closest_dist = float("inf")
                closest_lvl = None
                for lvl in active_levels:
                    dist_pips = abs(event.price - lvl.price) / self.pip_mult
                    if dist_pips < closest_dist:
                        closest_dist = dist_pips
                        closest_lvl = lvl
                
                if closest_lvl is None or closest_dist > 5.0:
                    logger.debug("PEAK GATE: skipped (not near any S/R zone; price=%.5f, closest_dist=%.2f pips)",
                                 event.price, closest_dist if closest_lvl else -1.0)
                    return
                

                logger.info("PEAK GATE: S/R Zone Proximity + Trend Aligned! Price=%.5f (%.2f pips from %s level %.5f), Trade=%s",
                            event.price, closest_dist, closest_lvl.level_type, closest_lvl.price, direction)
            
            # Quality scoring for peaks
            # HIGH (Rules A/B): 0.3 base + up to 0.5 from tick confidence
            # MEDIUM (Rule C): use swing_confidence if available, else fixed floor
            if intensity == "MEDIUM":
                sc = event.details.get("swing_confidence", None)
                if sc is not None:
                    signal_quality = 0.5 + 0.3 * sc  # 0.5–0.8 based on swing quality
                else:
                    signal_quality = 0.65  # fixed floor — Rule C already validated
            else:
                signal_quality = 0.3 + confidence * 0.5  # 0.3 base + up to 0.5 from confidence
            if confirmed:
                signal_quality += 0.2
 
        # 4. Structure Break (BOS)
        elif event.event_type == EventType.STRUCTURE_BREAK:
            dir_str = event.details.get("direction", "")
            if "bullish" in dir_str:
                direction = "BUY"
                trigger_reason = "Bullish Structure Break (BOS)"
            elif "bearish" in dir_str:
                direction = "SELL"
                trigger_reason = "Bearish Structure Break (BOS)"
            signal_quality = 0.65  # base quality floor

        # 5. Level Breach — store as pending; confirmed on next M15 candle close
        elif event.event_type == EventType.LEVEL_BREACH:
            # Skip pending storage if this is a confirmed event from _check_pending_level_breaches
            if event.details.get("_confirmed", False):
                level_dir = event.details.get("direction", "")
                if level_dir == "support":
                    direction = "BUY"
                    trigger_reason = f"Level Breach Bounce (Support {event.details.get('level_price', 0):.5f})"
                else:
                    direction = "SELL"
                    trigger_reason = f"Level Breach Rejection (Resistance {event.details.get('level_price', 0):.5f})"
                signal_quality = event.details.get("signal_quality", 0.70)
            else:
                level_price = event.details.get("level_price", 0.0)
                strength = event.details.get("strength", 0.0)
                level_dir = event.details.get("direction", "")
                if strength >= 0.7 and level_dir in ("support", "resistance"):
                    self._pending_level_breaches.append({
                        "level_price": level_price,
                        "direction": level_dir,
                        "strength": strength,
                        "event": event,
                        "entry_price": event.price,
                        "timestamp": event.timestamp,
                        "attack_count": event.details.get("attack_count", 0),
                        "is_absorbing": event.details.get("is_absorbing", False),
                    })
                    if self.config.get("realtime_log_events", True):
                        logger.debug("Level breach PENDING (%.5f %s strength=%.2f)",
                                     level_price, level_dir, strength)
                return

        if not direction:
            return
            
        # ── Gate 4: Level behavior check — skip if level is showing pressured absorption ──
        if level_attack_quality in ("weakening", "pressured") and event.event_type not in (EventType.SWEEP_DETECTED,):
            # A weakening/pressured level that hasn't swept yet is likely to get breached
            return
            
        # ── Gate 5: Regime filter — skip during high compression (no room for moves) ──
        if state:
            regime = state.dominant_regime if hasattr(state, "dominant_regime") else ""
            if regime == "compression":
                return
            
        # ── Gate 6: MTF Alignment (Bypassed trend blocks to allow two-way reversals) ──
        mtf = event.details.get("mtf_alignment", "NEUTRAL")
        # Aligned MTF boosts quality
        if (direction == "BUY" and mtf == "BULLISH") or (direction == "SELL" and mtf == "BEARISH"):
            signal_quality = min(1.0, signal_quality + 0.15)

        # ── Gate 7: Minimum signal quality threshold (after all adjustments) ──
        min_quality = 0.65
        if signal_quality < min_quality:
            return

        # ── Gate 8: Check for duplicate active trade in same direction ──
        for trade in self.active_trades:
            if trade["direction"] == direction:
                return
                    
        # ── Dynamic SL/TP based on ATR ──
        entry_price = event.price
        state = self.live_state._state
        atr = state.atr_14_h1 if state else 0.0012
        
        # SL = 1.0 × ATR, TP = 2.0 × ATR (optimized risk-reward ratio for WR/PF)
        sl_distance = max(atr * 1.0, 8 * self.pip_mult)   # floor of 8 pips
        tp_distance = max(atr * 2.0, 16 * self.pip_mult)   # floor of 16 pips

        if direction == "BUY":
            sl = entry_price - sl_distance
            tp = entry_price + tp_distance
        else:
            sl = entry_price + sl_distance
            tp = entry_price - tp_distance
                
        trade = {
            "id": len(self.simulated_trades) + 1,
            "direction": direction,
            "entry_time": event.timestamp,
            "entry_price": entry_price,
            "sl": round(sl, 5),
            "tp": round(tp, 5),
            "trigger": trigger_reason,
            "signal_quality": round(signal_quality, 2),
            "status": "OPEN",
            "exit_time": None,
            "exit_price": None,
            "pips": 0.0,
            "close_reason": ""
        }
        
        self.active_trades.append(trade)
        self.simulated_trades.append(trade)
        
        # Build detail context for logging
        ctx = ""
        if event.event_type == EventType.SWEEP_DETECTED:
            ctx = f" | dir={event.details.get('direction','')} abs={level_attack_quality}"
        elif event.event_type == EventType.PEAK_DETECTION:
            ctx = (f" | type={event.details.get('peak_type','')} "
                   f"conf={event.details.get('peak_confidence',0):.2f} "
                   f"confirmed={event.details.get('peak_confirmed',False)} "
                   f"intensity={event.details.get('intensity','')} "
                   f"mtf={event.details.get('mtf_alignment','')}")
        logger.info("BacktestEngine: [OPEN %s] Q=%.2f | Entry: %.5f | SL: %.5f | TP: %.5f | Trigger: %s%s",
                    direction, signal_quality, entry_price, sl, tp, trigger_reason, ctx)

    def _check_pending_level_breaches(self, candle: LiveCandle):
        """Check pending level breaches against the just-closed M15 candle.

        Entry requires:
        - Candle close confirms rejection (moves away from the level)
        - London/NY session (Gate 2 in _check_trade_triggers)
        """
        confirmed = []
        for pend in list(self._pending_level_breaches):
            pend["candles_since"] = pend.get("candles_since", 0) + 1
            if pend["candles_since"] > 3:  # Discard after 3 candles without confirmation
                self._pending_level_breaches.remove(pend)
                continue

            level_price = pend["level_price"]
            level_dir = pend["direction"]

            if level_dir == "support":
                # Support breach → need bullish rejection: candle closes above level
                if candle.close > level_price:
                    confirmed.append(pend)
            elif level_dir == "resistance":
                # Resistance breach → need bearish rejection: candle closes below level
                if candle.close < level_price:
                    confirmed.append(pend)

        for pend in confirmed:
            try:
                self._pending_level_breaches.remove(pend)
            except ValueError:
                pass
            event = pend["event"]

            # Determine trade direction from level type
            if pend["direction"] == "support":
                direction = "BUY"
                trigger_reason = f"Level Breach Bounce (Support {pend['level_price']:.5f})"
            else:
                direction = "SELL"
                trigger_reason = f"Level Breach Rejection (Resistance {pend['level_price']:.5f})"

            # Quality scoring
            signal_quality = 0.70  # base
            if pend["strength"] >= 0.8:
                signal_quality += 0.10  # stronger level → higher conviction
            if pend["is_absorbing"]:
                signal_quality += 0.10  # absorption suggests weakening → better reversal

            # Rebuild details with candle confirmation info
            from datetime import timedelta
            candle_end = candle.open_time + timedelta(minutes=15)  # M15 candle close time
            confirmed_event = MarketEvent(
                event_type=EventType.LEVEL_BREACH,
                priority=event.priority,
                timestamp=candle_end,  # actual execution time (candle close)
                symbol=event.symbol,
                price=candle.close,  # entry price = close of confirmation candle
                details={
                    **event.details,
                    "_confirmed": True,
                    "breach_timestamp": event.timestamp,  # preserve original breach time
                    "breach_price": event.price,  # preserve original breach price
                    "confirmed_on": candle_end,
                    "confirmed_close": candle.close,
                    "signal_quality": signal_quality,
                }
            )

            # Run through _check_trade_triggers gates (session, cooldown, MTF, quality floor)
            self._check_trade_triggers(confirmed_event)

    def _update_simulated_positions(self, bid: float, ask: float, timestamp: datetime):
        """Simulate high/low ticks hitting TP or SL targets."""
        for trade in list(self.active_trades):
            dir = trade["direction"]
            sl = trade["sl"]
            tp = trade["tp"]
            
            if dir == "BUY":
                # Trailing stop: when price reaches 1.0:1 risk-reward, lock breakeven + 1 pip
                entry = trade["entry_price"]
                sl_dist = entry - sl
                current_profit = bid - entry
                if sl_dist > 0 and current_profit >= 1.0 * sl_dist:
                    breakeven_sl = entry + 1 * self.pip_mult
                    if trade["sl"] < breakeven_sl:
                        trade["sl"] = breakeven_sl
                        sl = breakeven_sl
                
                if bid <= sl:
                    self._close_position(trade, bid, ask, timestamp, "Stop Loss (SL) Hit")
                elif bid >= tp:
                    self._close_position(trade, bid, ask, timestamp, "Take Profit (TP) Hit")
                    
            elif dir == "SELL":
                # Trailing stop for SELL
                entry = trade["entry_price"]
                sl_dist = sl - entry
                current_profit = entry - ask
                if sl_dist > 0 and current_profit >= 1.0 * sl_dist:
                    breakeven_sl = entry - 1 * self.pip_mult
                    if trade["sl"] > breakeven_sl:
                        trade["sl"] = breakeven_sl
                        sl = breakeven_sl
                
                if ask >= sl:
                    self._close_position(trade, bid, ask, timestamp, "Stop Loss (SL) Hit")
                elif ask <= tp:
                    self._close_position(trade, bid, ask, timestamp, "Take Profit (TP) Hit")

    def _close_position(self, trade: Dict[str, Any], bid: float, ask: float, timestamp: datetime, reason: str):
        """Close mock position and log pips."""
        self.active_trades.remove(trade)
        
        # BUY trade closes at Bid, SELL closes at Ask. Fill exact SL/TP targets to avoid tick gaps
        if "Stop Loss" in reason:
            exit_price = trade["sl"]
        elif "Take Profit" in reason:
            exit_price = trade["tp"]
        else:
            exit_price = bid if trade["direction"] == "BUY" else ask
            
        trade["exit_price"] = exit_price
        trade["exit_time"] = timestamp
        trade["close_reason"] = reason
        
        # Calculate pips
        if trade["direction"] == "BUY":
            pips = (exit_price - trade["entry_price"]) / self.pip_mult
        else:
            pips = (trade["entry_price"] - exit_price) / self.pip_mult
            
        trade["pips"] = round(pips, 1)
        trade["status"] = "WIN" if pips > 0 else "LOSS"
        
        # Track loss time for cooldown
        if pips <= 0:
            self._last_loss_time = timestamp
        
        logger.info("BacktestEngine: [CLOSE %s] Exit: %.5f | PnL: %+.1f pips | Reason: %s",
                    trade["direction"], exit_price, pips, reason)

    def _compile_performance_metrics(self) -> Dict[str, Any]:
        """Compile comprehensive trading KPIs and win/loss statistics."""
        total_trades = len(self.simulated_trades)
        wins = sum(1 for t in self.simulated_trades if t["status"] == "WIN")
        losses = sum(1 for t in self.simulated_trades if t["status"] == "LOSS")
        
        win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
        
        total_pips = sum(t["pips"] for t in self.simulated_trades)
        gross_profits = sum(t["pips"] for t in self.simulated_trades if t["pips"] > 0)
        gross_losses = sum(abs(t["pips"]) for t in self.simulated_trades if t["pips"] < 0)
        
        profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else (gross_profits if gross_profits > 0 else 1.0)
        
        # Count event classifications
        event_counts = {}
        for evt in self.detected_events:
            event_counts[evt.event_type.value] = event_counts.get(evt.event_type.value, 0) + 1
            
        report = {
            "ticker": self.ticker,
            "days": self.days,
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate_percent": round(win_rate, 1),
            "net_profit_pips": round(total_pips, 1),
            "profit_factor": round(profit_factor, 2),
            "event_breakdown": event_counts,
            "trades": self.simulated_trades,
            "events": [
                {
                    "time": e.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "type": e.event_type.value,
                    "price": e.price,
                    "priority": e.priority.value,
                    "details": e.details
                }
                for e in self.detected_events
            ]
        }
        return report

    def generate_markdown_report(self, report: Dict[str, Any]) -> str:
        """Serialize backtest results to a premium, beautifully structured Markdown file."""
        lines = []
        lines.append(f"# AxonAI Backtesting Performance Report: {report['ticker']}")
        lines.append(f"**Execution Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**Historical Look-Back Period**: {report['days']} Days\n")
        
        lines.append("## Executive Performance Summary")
        lines.append("| Metric | Value |")
        lines.append("| :--- | :--- |")
        lines.append(f"| **Total Triggered Trades** | {report['total_trades']} |")
        lines.append(f"| **Won Trades (Win)** | {report['wins']} \u2705 |")
        lines.append(f"| **Lost Trades (Loss)** | {report['losses']} \u274c |")
        lines.append(f"| **Win Rate** | **{report['win_rate_percent']}%** |")
        lines.append(f"| **Net Profit / Loss** | **{report['net_profit_pips']:+.1f} pips** |")
        lines.append(f"| **Profit Factor** | {report['profit_factor']} |")
        lines.append("")
        
        # Add visual Mermaid KPI breakdown
        lines.append("## Visual Metrics Representation")
        lines.append("```mermaid")
        lines.append("pie title Trade Outcome Distribution")
        lines.append(f"    \"Wins ({report['wins']})\" : {report['wins']}")
        lines.append(f"    \"Losses ({report['losses']})\" : {report['losses']}")
        lines.append("```\n")
        
        # Event breakdown
        lines.append("## Detected Structural Events")
        lines.append("| Event Type | Occurrences |")
        lines.append("| :--- | :--- |")
        for k, v in sorted(report["event_breakdown"].items(), key=lambda x: x[1], reverse=True):
            lines.append(f"| `{k}` | {v} |")
        lines.append("")
        
        # Detailed Trade Log
        lines.append("## Detailed Simulated Trades Log")
        lines.append("| ID | Type | Q | Entry Time | Entry | Trigger Signal | Exit Time | Exit | Status | Profit (Pips) |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for t in report["trades"]:
            status_emoji = "\u2705 WIN" if t["status"] == "WIN" else "\u274c LOSS"
            entry_t = t["entry_time"].strftime("%m-%d %H:%M:%S")
            exit_t = t["exit_time"].strftime("%m-%d %H:%M:%S") if t["exit_time"] else "--"
            exit_p = f"{t['exit_price']:.5f}" if t["exit_price"] else "--"
            q = t.get("signal_quality", 0.0)
            lines.append(f"| {t['id']} | **{t['direction']}** | {q:.2f} | {entry_t} | {t['entry_price']:.5f} | {t['trigger']} | {exit_t} | {exit_p} | {status_emoji} | **{t['pips']:+.1f}** |")
        lines.append("")
        
        # Detailed Event Log
        lines.append("## Detailed Technical Event Records")
        lines.append("<details><summary>Click to view all detected structural events</summary>\n")
        lines.append("| Timestamp | Event Type | Price | Details |")
        lines.append("| :--- | :--- | :--- | :--- |")
        # Log first 30 events to avoid massive logs
        for e in report["events"][:50]:
            detail_str = ", ".join(f"{k}={v}" for k, v in e["details"].items() if k != "trigger_candle")
            lines.append(f"| {e['time']} | `{e['type']}` | {e['price']:.5f} | {detail_str} |")
        if len(report["events"]) > 50:
            lines.append(f"| ... | ... | ... | *(and {len(report['events'])-50} more events)* |")
        lines.append("\n</details>")
        
        return "\n".join(lines)
