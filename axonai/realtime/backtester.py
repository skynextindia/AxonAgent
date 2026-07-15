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
from axonai.realtime.live_state import LiveWorldState, LiveMarketEvidence
from axonai.realtime.reversal_model import ReversalModel
from axonai.realtime.trade_analytics import TradeAnalytics
from axonai.dataflows.mt5_data import mt5_initialize, _to_mt5_symbol, _ensure_symbol_visible, _fetch_bars

logger = logging.getLogger(__name__)

class BacktestEngine:
    """Historical data simulator and backtesting engine for AxonAI."""

    def __init__(self, ticker: str = "EURUSD=X", days: int = 5, config: Optional[dict] = None):
        self.ticker = ticker
        self.days = days
        self.config = config or {}
        self.config["backtest_mode"] = True
        
        # Clean ticker suffix for pips
        ticker_clean = ticker.upper().replace("=X", "").replace("/", "")
        self.is_jpy = "JPY" in ticker_clean or "XAU" in ticker_clean
        self.pip_mult = 0.01 if self.is_jpy else 0.0001
        
        # Load per-pair calibration params
        try:
            import json as _json, os as _os
            _cp = _os.path.join("reports", f"calibration_params_{ticker_clean}.json")
            if _os.path.exists(_cp):
                with open(_cp, "r", encoding="utf-8") as _f:
                    _params = _json.load(_f) or {}
                for _k, _v in _params.items():
                    self.config.setdefault(_k, _v)
                logger.info("BacktestEngine: Loaded %d calibration params from %s", len(_params), _cp)
        except Exception as _e:
            logger.warning("BacktestEngine: Calibration params load failed (%s)", _e)

        # Scale defaults (e.g. for Gold)
        _scale = float(self.config.get("pair_move_scale") or (10.0 if "XAU" in ticker_clean else 1.0))
        self.config["pair_move_scale"] = _scale
        self.config.setdefault("displacement_exhaustion_min_move_pips", 3.0 * _scale)
        self.config.setdefault("displacement_trend_net_pips", 2.0 * _scale)
        self.config.setdefault("context_exhaustion_net_max_pips", 2.0 * _scale)
        self.config.setdefault("microstructure_velocity_min", 1.5 * _scale)
        self.config.setdefault("absorption_max_move_pips", 2.0 * _scale)
        self.config.setdefault("level_rejection_vel_min", 5.0 * _scale)
        self.config.setdefault("level_strong_rejection_vel", 8.0 * _scale)
        self.config.setdefault("vol_pips_ref", 1.0 * _scale)
        
        # Apply entry defaults
        if "XAU" in ticker_clean:
            self.config.setdefault("entry_max_velocity_pct", self.config.get("entry_max_velocity_pct_gold", 30.0))
            self.config.setdefault("entry_min_decay_ratio", self.config.get("entry_min_decay_ratio_gold", 0.40))
            self.config.setdefault("entry_max_tick_efficiency", self.config.get("entry_max_tick_efficiency_gold", 0.30))
        else:
            self.config.setdefault("entry_max_velocity_pct", 100.0)
            self.config.setdefault("entry_min_decay_ratio", 0.0)
            self.config.setdefault("entry_max_tick_efficiency", 1.0)
        
        # Initialize Event Queue and state tracking
        self.event_queue: queue.Queue[MarketEvent] = queue.Queue()
        self.detected_events: List[MarketEvent] = []
        self.simulated_trades: List[Dict[str, Any]] = []
        self.active_trades: List[Dict[str, Any]] = []
        self._pending_level_breaches: list = []
        
        # Initialize Core Real-Time Components
        self.live_state = LiveWorldState(self.ticker, self.config)
        self.live_evidence = LiveMarketEvidence(self.ticker, self.config)
        
        # Initialize Reversal Engine
        self.reversal_model = ReversalModel(
            pip_mult=self.pip_mult,
            config=self.config
        )
        self.trade_analytics = TradeAnalytics(log_dir="reports/backtest")
        
        # Override initialization constraints
        self.live_state._initialized = True
        self.live_evidence._initialized = True

    def load_historical_data(self) -> Tuple[List[LiveCandle], List[Tuple[float, float, datetime]]]:
        """Fetch real data from MT5 if connected, otherwise generate synthetic market dataset."""
        connected = False
        try:
            if mt5_initialize(self.config.get("mt5_terminal_path")):
                connected = True
        except Exception:
            pass

        if connected:
            logger.info("BacktestEngine: MT5 connected. Loading real historical bars for %s", self.ticker)
            return self._load_real_data()
        else:
            logger.info("BacktestEngine: MT5 offline. Generating realistic high-fidelity synthetic market dataset.")
            return self._generate_synthetic_data()

    def _load_real_data(self) -> Tuple[List[LiveCandle], List[Tuple[float, float, datetime]]]:
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
        ticks: List[Tuple[float, float, datetime]] = []
        
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
            # Space ticks by 0.05 seconds, ending at candle close time, to simulate real tick speed for VelocityNormalizer
            candle_close_time = t + timedelta(minutes=15)
            
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
                
            for idx, price in enumerate(sub_prices):
                tick_time = candle_close_time - timedelta(seconds=(steps - 1 - idx) * 0.05)
                ticks.append((price - 0.00005, price + 0.00005, tick_time))
                
        return candles, ticks

    def _generate_synthetic_data(self) -> Tuple[List[LiveCandle], List[Tuple[float, float, datetime]]]:
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
        ticks: List[Tuple[float, float, datetime]] = []
        
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
            # Space ticks by 0.05 seconds, ending at candle close time, to simulate real tick speed for VelocityNormalizer
            candle_close_time = bar_time + timedelta(minutes=15)
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
            
            for idx, pr in enumerate(sub_prices):
                tick_time = candle_close_time - timedelta(seconds=(steps - 1 - idx) * 0.05)
                ticks.append((round(pr - 0.00005, 5), round(pr + 0.00005, 5), tick_time))
                
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
            self.live_state.on_candle_close(c)
            self.live_evidence.on_candle_close(c)
            self.reversal_model.on_candle_close(c)
            
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
                self.live_state.on_candle_close(h1_candle)
                self.live_evidence.on_candle_close(h1_candle)
                self.reversal_model.on_candle_close(h1_candle)
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
                self.live_state.on_candle_close(h4_candle)
                self.live_evidence.on_candle_close(h4_candle)
                self.reversal_model.on_candle_close(h4_candle)
                h4_chunk = []
                
        return warmup_limit

    def run(self) -> Dict[str, Any]:
        """Execute the backtest simulation sequentially, detecting events and managing trades."""
        # Reset trackers
        self.detected_events.clear()
        self.simulated_trades.clear()
        self.active_trades.clear()
        self._last_loss_time = None  # track last loss for cooldown
        self._pending_limit_order = None
        
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
        
        # Seed initial live_state fields directly (WorldState removed — pure-math engine)
        class DictLikeObject:
            def __init__(self, data: dict):
                self.__dict__.update(data)
            def get(self, key, default=None):
                return self.__dict__.get(key, default)
            def __getitem__(self, key):
                return self.__dict__[key]
            def __setitem__(self, key, val):
                self.__dict__[key] = val
            def __contains__(self, key):
                return key in self.__dict__

        self.live_state._state = DictLikeObject({
            "regime_scores": {"trending": 0.3, "ranging": 0.7, "breakout": 0.0, "compression": 0.0, "panic": 0.0},
            "dominant_regime": "ranging",
            "regime_confidence": 0.7,
            "volatility_regime": "medium",
            "atr_14_h1": computed_atr,
            "session": "london",
            "session_quality": 0.8,
            "session_penalty": 1.0,
            "hours_since_london_open": 2.0,
            "spread_pips": 1.0,
            "spread_safe": True,
            "eur_strength": 0.0,
            "usd_strength": 0.0,
            "belief_score": 0.8,
            "should_run_graph": True,
            "abort_reason": "",
        })
        
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
        self.reversal_model.sync_levels(self.live_evidence.price_levels)
        
        sh_price = key_lvls[-1] if key_lvls else 1.1550
        sl_price = key_lvls[0] if key_lvls else 1.1480
        
        # Derive trend direction from first half vs second half of data
        half = len(candles) // 2
        first_half_avg = np.mean([c.close for c in candles[:half]])
        second_half_avg = np.mean([c.close for c in candles[half:]])
        trend_h1 = "up" if second_half_avg > first_half_avg + 5 * self.pip_mult else (
                   "down" if second_half_avg < first_half_avg - 5 * self.pip_mult else "sideways")
        
        # Seed initial evidence fields directly on LiveMarketEvidence
        self.live_evidence.swing_highs = [{"price": sh_price, "time": candles[0].open_time.strftime("%Y-%m-%d %H:%M")}]
        self.live_evidence.swing_lows  = [{"price": sl_price, "time": candles[0].open_time.strftime("%Y-%m-%d %H:%M")}]
        self.live_evidence.key_levels  = list(key_lvls)
        
        # Warm up candle history & aggregate deques
        warmup_limit = self._prewarm_candle_history(candles)
        
        # Calculate initial institutional levels from the prewarmed historical candles
        self.live_evidence._calculate_initial_institutional_levels()
        self.reversal_model.sync_levels(self.live_evidence.price_levels)
        
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
        
        _prog_every = self.config.get("backtest_progress_every", 0)
        _total_bars = len(candles)
        for bar_idx, candle in enumerate(candles[warmup_limit:], start=warmup_limit):
            bar_start = candle.open_time
            bar_end = bar_start + timedelta(minutes=15)

            # Optional progress reporting for long backtests (config-gated; off by default)
            if _prog_every and bar_idx % _prog_every == 0:
                pct = 100.0 * (bar_idx - warmup_limit) / max(1, _total_bars - warmup_limit)
                print(f"PROGRESS {pct:5.1f}% | bar {bar_idx}/{_total_bars} | "
                      f"ticks {tick_index}/{total_ticks} | trades {len(self.simulated_trades)}",
                      flush=True)
            
            # Feed ticks that fall within this bar's time window
            while tick_index < total_ticks:
                bid, ask, tick_time = ticks[tick_index]
                if tick_time > bar_end:
                    break
                    
                # Update incrementally maintained state
                self.live_state.on_tick(bid, ask, tick_time)
                self.live_evidence.on_tick(bid, ask, tick_time)
                self.reversal_model.sync_levels(self.live_evidence.price_levels)

                # 1. Process Tick through Pure-Math Reversal Engine
                snapshot = self.reversal_model.on_tick((bid+ask)/2.0, tick_time, 1, bid=bid, ask=ask)
                
                # Check for pending limit order placement, execution and cancellation
                state = snapshot.entry_decision.state
                entry_style = self.config.get("realtime_entry_style", "limit")
                if self.config.get("realtime_instant_execution", False):
                    entry_style = "instant"

                use_market = entry_style in ("instant", "confirmed")
                should_enter = (state == "TRIGGERED" and getattr(snapshot.entry_decision, "is_valid_entry", True))

                # 1. Place Limit Order or Execute Market Order on entry trigger
                if should_enter:
                    if use_market:
                        if len(self.active_trades) == 0:
                            anomaly_price = self.reversal_model.entry._anomaly_price
                            direction = self.reversal_model.entry._anomaly_direction
                            
                            state_obj = self.live_state._state
                            atr = state_obj.atr_14_h1 if state_obj else 0.0012
                            buffer = 1.0 * self.pip_mult
                            spread = ask - bid
                            
                            if direction == "BUY":
                                sl_distance = max(8 * self.pip_mult, atr * self.config.get("sl_atr_multiple", 1.0))
                                sl = snapshot.price - sl_distance
                                tp_distance = max(16 * self.pip_mult, sl_distance * self.config.get("tp_atr_multiple", 2.0))
                                tp = snapshot.price + tp_distance
                            else:
                                sl_distance = max(8 * self.pip_mult, atr * self.config.get("sl_atr_multiple", 1.0))
                                sl = snapshot.price + sl_distance
                                tp_distance = max(16 * self.pip_mult, sl_distance * self.config.get("tp_atr_multiple", 2.0))
                                tp = snapshot.price - tp_distance
                                
                            # Check Gates (cooldowns & concurrency)
                            is_blocked = False
                            vol_pips = getattr(snapshot.velocity, "vol_pips", 3.0)
                            zone_width = vol_pips * 1.5
                            cooldown_seconds = self.config.get("realtime_cooldown_seconds", self.config.get("cooldown_seconds", 900))
                            for trade in self.simulated_trades:
                                elapsed = (tick_time - trade["entry_time"]).total_seconds()
                                if elapsed < cooldown_seconds:
                                    if trade["direction"] == direction:
                                        dist = abs(snapshot.price - trade["entry_price"]) / self.pip_mult
                                        if dist <= zone_width:
                                            is_blocked = True
                                            logger.info("BacktestEngine: Market entry blocked by Gate 3 (Cooldown)")
                                            break
                            if not is_blocked:
                                loss_cooldown = self.config.get("realtime_loss_cooldown_minutes", self.config.get("loss_cooldown_minutes", 45))
                                for trade in self.simulated_trades:
                                    if trade.get("status") == "LOSS" and "exit_time" in trade:
                                        minutes_since_loss = (tick_time - trade["exit_time"]).total_seconds() / 60.0
                                        if minutes_since_loss < loss_cooldown:
                                            if trade["direction"] == direction:
                                                dist = abs(snapshot.price - trade["entry_price"]) / self.pip_mult
                                                if dist <= zone_width:
                                                    is_blocked = True
                                                    logger.info("BacktestEngine: Market entry blocked by Gate 3b (Loss Cooldown)")
                                                    break
                            if not is_blocked:
                                for trade in self.active_trades:
                                    if trade["direction"] == direction:
                                        is_blocked = True
                                        break
                                        
                            if not is_blocked:
                                logger.info("BacktestEngine: Market order FILLED on sweep (style: %s) at %.5f (SL=%.5f TP=%.5f)", entry_style, snapshot.price, sl, tp)
                                self._open_position(
                                    direction, snapshot.price, tick_time, 
                                    f"Market Sweep ({entry_style}): {snapshot.entry_decision.reason}", 
                                    snapshot.entry_decision.signal_quality,
                                    sl=sl,
                                    tp=tp
                                )
                                self.reversal_model.register_trade(
                                    len(self.simulated_trades), direction, snapshot.price, 
                                    sl, tp,
                                    reason=f"Market Sweep ({entry_style}): {snapshot.entry_decision.reason}"
                                )
                                self.reversal_model.entry.reset()
                        self.reversal_model.entry.reset()
                    else:
                        if self._pending_limit_order is None:
                            anomaly_price = self.reversal_model.entry._anomaly_price
                            direction = self.reversal_model.entry._anomaly_direction
                            
                            state_obj = self.live_state._state
                            atr = state_obj.atr_14_h1 if state_obj else 0.0012
                            buffer = 1.0 * self.pip_mult
                            spread = ask - bid
                            
                            if direction == "BUY":
                                sl_distance = max(8 * self.pip_mult, atr * self.config.get("sl_atr_multiple", 1.0))
                                sl = anomaly_price - sl_distance
                                tp_distance = max(16 * self.pip_mult, sl_distance * self.config.get("tp_atr_multiple", 2.0))
                                tp = anomaly_price + tp_distance
                            else:
                                sl_distance = max(8 * self.pip_mult, atr * self.config.get("sl_atr_multiple", 1.0))
                                sl = anomaly_price + sl_distance
                                tp_distance = max(16 * self.pip_mult, sl_distance * self.config.get("tp_atr_multiple", 2.0))
                                tp = anomaly_price - tp_distance
                                
                            # Check Gates
                            is_blocked = False
                            vol_pips = getattr(snapshot.velocity, "vol_pips", 3.0)
                            zone_width = vol_pips * 1.5
                            cooldown_seconds = self.config.get("realtime_cooldown_seconds", self.config.get("cooldown_seconds", 900))
                            for trade in self.simulated_trades:
                                elapsed = (tick_time - trade["entry_time"]).total_seconds()
                                if elapsed < cooldown_seconds:
                                    if trade["direction"] == direction:
                                        dist = abs(anomaly_price - trade["entry_price"]) / self.pip_mult
                                        if dist <= zone_width:
                                            is_blocked = True
                                            logger.info("BacktestEngine: Limit order blocked by Gate 3 (Cooldown)")
                                            break
                            if not is_blocked:
                                loss_cooldown = self.config.get("realtime_loss_cooldown_minutes", self.config.get("loss_cooldown_minutes", 45))
                                for trade in self.simulated_trades:
                                    if trade.get("status") == "LOSS" and "exit_time" in trade:
                                        minutes_since_loss = (tick_time - trade["exit_time"]).total_seconds() / 60.0
                                        if minutes_since_loss < loss_cooldown:
                                            if trade["direction"] == direction:
                                                dist = abs(anomaly_price - trade["entry_price"]) / self.pip_mult
                                                if dist <= zone_width:
                                                    is_blocked = True
                                                    logger.info("BacktestEngine: Limit order blocked by Gate 3b (Loss Cooldown)")
                                                    break
                            if not is_blocked:
                                for trade in self.active_trades:
                                    if trade["direction"] == direction:
                                        is_blocked = True
                                        break
                                        
                            if not is_blocked:
                                self._pending_limit_order = {
                                    "limit_price": anomaly_price,
                                    "direction": direction,
                                    "sl": sl,
                                    "tp": tp,
                                    "reason": f"Limit Order: {snapshot.entry_decision.reason}",
                                    "quality": snapshot.entry_decision.signal_quality
                                }
                                logger.info("BacktestEngine: Placed pending limit order at %.5f (SL=%.5f TP=%.5f)", anomaly_price, sl, tp)

                # 2. Check Fill for active pending limit order (only if not using instant execution)
                if not use_market and self._pending_limit_order is not None:
                    limit_price = self._pending_limit_order["limit_price"]
                    direction = self._pending_limit_order["direction"]
                    is_filled = False
                    if direction == "BUY" and ask <= limit_price:
                        is_filled = True
                    elif direction == "SELL" and bid >= limit_price:
                        is_filled = True
                        
                    if is_filled:
                        logger.info("BacktestEngine: Pending limit order FILLED at %.5f", limit_price)
                        self._open_position(
                            direction, limit_price, tick_time, 
                            self._pending_limit_order["reason"], 
                            self._pending_limit_order["quality"],
                            sl=self._pending_limit_order["sl"],
                            tp=self._pending_limit_order["tp"]
                        )
                        self.reversal_model.register_trade(
                            len(self.simulated_trades), direction, limit_price, 
                            self._pending_limit_order["sl"], self._pending_limit_order["tp"],
                            reason=self._pending_limit_order["reason"]
                        )
                        self._pending_limit_order = None
                        self.reversal_model.entry.reset()

                if not use_market and state in ("INVALIDATED", "IDLE") and self._pending_limit_order is not None:
                    logger.info("BacktestEngine: Cancelled pending limit order at %.5f", self._pending_limit_order["limit_price"])
                    self._pending_limit_order = None
                    
                # Check for adaptive exit triggers
                if snapshot.exit_decision.should_exit:
                    self._execute_adaptive_exit(snapshot)
                    
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
            self.live_state.on_candle_close(candle)
            self.live_evidence.on_candle_close(candle)
            self.reversal_model.sync_levels(self.live_evidence.price_levels)
            self.reversal_model.on_candle_close(candle)
            
            # Aggregate and trigger H1 closes
            if candle.open_time.minute == 45:
                h1_hour = candle.open_time.hour
                h1_day = candle.open_time.date()
                h1_candles_chunk = [c for c in candles[max(0, bar_idx - 6):bar_idx+1] 
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
                    self.live_state.on_candle_close(h1_candle)
                    self.live_evidence.on_candle_close(h1_candle)
                    self.reversal_model.sync_levels(self.live_evidence.price_levels)
                    self.reversal_model.on_candle_close(h1_candle)
                    
            # Aggregate and trigger H4 closes
            if candle.open_time.minute == 45 and (candle.open_time.hour + 1) % 4 == 0:
                h4_group = candle.open_time.hour // 4
                h4_day = candle.open_time.date()
                h4_candles_chunk = [c for c in candles[max(0, bar_idx - 20):bar_idx+1] 
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
                    self.live_state.on_candle_close(h4_candle)
                    self.live_evidence.on_candle_close(h4_candle)
                    self.reversal_model.sync_levels(self.live_evidence.price_levels)
                    self.reversal_model.on_candle_close(h4_candle)
            
            # Check pending level breaches for candle-close confirmation
            # No longer needed, handled by ReversalModel internally
        # Close any open positions at market close on last tick
        if self.active_trades:
            final_bid = ticks[-1][0]
            final_ask = ticks[-1][1]
            final_time = ticks[-1][2]
            for trade in list(self.active_trades):
                self._close_position(trade, final_bid, final_ask, final_time, "Market Close")
                
        # Write exit stats to CSV
        if not self.config.get("backtest_mode", False):
            self.reversal_model.exit_stats.to_csv("reports/exit_stats.csv")

        # Calculate Backtesting Performance Metrics
        report = self._compile_performance_metrics()
        return report

    def _check_trade_triggers(self, snapshot: Any):
        """Evaluate engine decisions with backtester specific constraints."""
        # ── Gate 1: Max concurrent trades ──
        if len(self.active_trades) >= 1:
            logger.info("BacktestEngine: Entry blocked by Gate 1 (Max concurrent trades)")
            return

        # ── Gate 2: London/NY sessions only (intraday) ──
        state = self.live_state._state
        if state and state.session not in ("london", "overlap", "newyork"):
            logger.info("BacktestEngine: Entry blocked by Gate 2 (Session: %s)", state.session if state else "None")
            return

        # ── Gate 3: level-aware and direction-aware entry cooldown ──
        evt_time = datetime.fromtimestamp(snapshot.timestamp, tz=timezone.utc).replace(tzinfo=None) if isinstance(snapshot.timestamp, (int, float)) else snapshot.timestamp
        evt_price = snapshot.price
        vol_pips = getattr(snapshot.velocity, "vol_pips", 3.0)
        zone_width = vol_pips * 1.5
        direction = snapshot.entry_decision.direction

        if not direction:
            return

        cooldown_seconds = self.config.get("realtime_cooldown_seconds", self.config.get("cooldown_seconds", 900))
        for trade in self.simulated_trades:
            elapsed = (evt_time - trade["entry_time"]).total_seconds()
            if elapsed < cooldown_seconds:
                if trade["direction"] == direction:
                    dist = abs(evt_price - trade["entry_price"]) / self.pip_mult
                    if dist <= zone_width:
                        logger.info("BacktestEngine: Entry blocked by Gate 3 (Level+Direction Cooldown: %.1f pips from entry)", dist)
                        return

        # ── Gate 3b: level-aware and direction-aware loss cooldown ──
        loss_cooldown = self.config.get("realtime_loss_cooldown_minutes", self.config.get("loss_cooldown_minutes", 45))
        for trade in self.simulated_trades:
            if trade.get("status") == "LOSS" and "exit_time" in trade:
                minutes_since_loss = (evt_time - trade["exit_time"]).total_seconds() / 60.0
                if minutes_since_loss < loss_cooldown:
                    if trade["direction"] == direction:
                        dist = abs(evt_price - trade["entry_price"]) / self.pip_mult
                        if dist <= zone_width:
                            logger.info("BacktestEngine: Entry blocked by Gate 3b (Level+Direction Loss Cooldown: %.1f mins left, %.1f pips from entry)", loss_cooldown - minutes_since_loss, dist)
                            return

        direction = snapshot.entry_decision.direction
        trigger_reason = f"Reversal Engine: {snapshot.entry_decision.reason}"
        signal_quality = snapshot.entry_decision.signal_quality

        if not direction:
            return

        # ── Gate 7: Minimum signal quality threshold (after all adjustments) ──
        min_quality = self.config.get("realtime_min_signal_quality", self.config.get("min_signal_quality", 0.55))
        if signal_quality < min_quality:
            logger.info("BacktestEngine: Entry blocked by Gate 7 (Signal quality: %.2f < %.2f)", signal_quality, min_quality)
            return

        # ── Gate 8: Check for duplicate active trade in same direction ──
        for trade in self.active_trades:
            if trade["direction"] == direction:
                return
                    
        # ── Structure-based SL/TP based on Anomaly Price ──
        entry_price = snapshot.price
        state = self.live_state._state
        atr = state.atr_14_h1 if state else 0.0012
        anomaly_price = self.reversal_model.entry._anomaly_price
        spread = ask - bid
        buffer = 1.0 * self.pip_mult

        sl_distance = max(8 * self.pip_mult, atr * self.config.get("sl_atr_multiple", 1.0))
        tp_distance = max(16 * self.pip_mult, sl_distance * self.config.get("tp_atr_multiple", 2.0))
        if direction == "BUY":
            sl = entry_price - sl_distance
            tp = entry_price + tp_distance
        else:
            sl = entry_price + sl_distance
            tp = entry_price - tp_distance
                
        self._open_position(direction, entry_price, evt_time, trigger_reason, signal_quality, sl=sl, tp=tp)
        self.reversal_model.register_trade(len(self.simulated_trades), direction, entry_price, sl, tp, reason=trigger_reason)

    def _execute_adaptive_exit(self, snapshot: Any):
        """Execute adaptive exit logic."""
        decision = snapshot.exit_decision
        if not decision: return
        
        if decision.action == "ADJUST_SL":
            for trade in self.active_trades:
                trade["sl"] = decision.suggested_sl
        elif decision.action == "CLOSE_NOW":
            evt_time = datetime.fromtimestamp(snapshot.timestamp, tz=timezone.utc).replace(tzinfo=None) if isinstance(snapshot.timestamp, (int, float)) else snapshot.timestamp
            for trade in list(self.active_trades):
                self._close_position(trade, snapshot.price, snapshot.price, evt_time, f"Adaptive Exit: {decision.reason}")
                self.reversal_model.clear_trade()

    def _open_position(self, direction: str, entry_price: float, timestamp: datetime, reason: str, quality: float, sl: float, tp: float):
        """Open a new simulated position."""
        trade = {
            "id": len(self.simulated_trades) + 1,
            "direction": direction,
            "entry_time": timestamp,
            "entry_price": entry_price,
            "sl": round(sl, 5),
            "tp": round(tp, 5),
            "hard_sl": round(sl, 5),
            "hard_tp": round(tp, 5),
            "trigger": reason,
            "signal_quality": round(quality, 2),
            "status": "OPEN",
            "exit_time": None,
            "exit_price": None,
            "pips": 0.0,
            "close_reason": ""
        }
        
        self.active_trades.append(trade)
        self.simulated_trades.append(trade)
        
        logger.info("BacktestEngine: [OPEN %s] Q=%.2f | Entry: %.5f | SL: %.5f | TP: %.5f | Trigger: %s",
                    direction, quality, entry_price, sl, tp, reason)

    def _update_simulated_positions(self, bid: float, ask: float, timestamp: datetime):
        """Simulate high/low ticks hitting TP or SL targets."""
        for trade in list(self.active_trades):
            dir = trade["direction"]
            sl = trade["sl"]
            tp = trade["tp"]
            hard_sl = trade.get("hard_sl", sl)
            hard_tp = trade.get("hard_tp", tp)
            
            if dir == "BUY":
                if bid <= sl:
                    self._close_position(trade, bid, ask, timestamp, "Stop Loss (SL) Hit")
                elif bid <= hard_sl:
                    self._close_position(trade, bid, ask, timestamp, "Hard SL (backstop) Hit")
                elif bid >= hard_tp:
                    self._close_position(trade, bid, ask, timestamp, "Hard TP (backstop) Hit")
                    
            elif dir == "SELL":
                if ask >= sl:
                    self._close_position(trade, bid, ask, timestamp, "Stop Loss (SL) Hit")
                elif ask >= hard_sl:
                    self._close_position(trade, bid, ask, timestamp, "Hard SL (backstop) Hit")
                elif bid <= hard_tp:
                    self._close_position(trade, bid, ask, timestamp, "Hard TP (backstop) Hit")

    def _close_position(self, trade: Dict[str, Any], bid: float, ask: float, timestamp: datetime, reason: str):
        """Close mock position and log pips."""
        self.active_trades.remove(trade)
        self.reversal_model.clear_trade()
        
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
