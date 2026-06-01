"""Real-time tick ingestion engine.

Continuously polls MT5 for raw tick data and builds live OHLCV candles
in memory across M1, M5, M15, and H1 timeframes.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from axonai.realtime.event_types import LiveCandle

logger = logging.getLogger(__name__)

# Timeframe period in seconds
_TF_PERIODS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "H1": 3600,
    "H4": 14400,
}



class CandleBuilder:
    """Builds OHLCV candles from raw ticks for a single timeframe."""

    def __init__(self, timeframe: str, max_history: int = 500):
        self.timeframe = timeframe
        self.period_seconds = _TF_PERIODS[timeframe]
        self.history: deque[LiveCandle] = deque(maxlen=max_history)
        self.current: Optional[LiveCandle] = None

    def _period_start(self, ts: datetime) -> datetime:
        """Compute the period start time for a given timestamp."""
        # Use timezone-aware UTC conversion to prevent OS local timezone or DST shifts!
        total_seconds = int(ts.replace(tzinfo=timezone.utc).timestamp())
        period_start_sec = (total_seconds // self.period_seconds) * self.period_seconds
        return datetime.fromtimestamp(period_start_sec, tz=timezone.utc).replace(tzinfo=None)

    def feed_tick(self, price: float, volume: int, timestamp: datetime) -> Optional[LiveCandle]:
        """Process a tick. Returns a closed candle if the period boundary was crossed."""
        period_start = self._period_start(timestamp)
        closed_candle = None

        if self.current is None:
            # First tick ever
            self.current = LiveCandle(
                timeframe=self.timeframe,
                open_time=period_start,
                open=price, high=price, low=price, close=price,
                volume=volume,
            )
            return None

        if period_start > self.current.open_time:
            # Period boundary crossed — close current candle
            self.current.is_closed = True
            closed_candle = self.current
            self.history.append(closed_candle)

            # Start new candle
            self.current = LiveCandle(
                timeframe=self.timeframe,
                open_time=period_start,
                open=price, high=price, low=price, close=price,
                volume=volume,
            )
        else:
            # Same period — update OHLCV
            self.current.high = max(self.current.high, price)
            self.current.low = min(self.current.low, price)
            self.current.close = price
            self.current.volume += volume

        return closed_candle

    def get_closes(self, count: int = 50) -> List[float]:
        """Return the last N closed candle close prices."""
        candles = list(self.history)
        return [c.close for c in candles[-count:]]

    def get_highs(self, count: int = 50) -> List[float]:
        """Return the last N closed candle high prices."""
        candles = list(self.history)
        return [c.high for c in candles[-count:]]

    def get_lows(self, count: int = 50) -> List[float]:
        """Return the last N closed candle low prices."""
        candles = list(self.history)
        return [c.low for c in candles[-count:]]


class TickEngine(threading.Thread):
    """Dedicated thread that polls MT5 for ticks and feeds candle builders.

    Callbacks:
        on_tick_callback(bid, ask, timestamp): Called on every new tick.
        on_candle_close_callback(candle): Called when any timeframe candle closes.
    """

    def __init__(self, symbol: str, *args, **kwargs):
        super().__init__(daemon=True, name=f"TickEngine-{symbol}")
        self.symbol = symbol  # MT5 symbol e.g. "EURUSDm"
        
        # Support both (symbol, config) and (symbol, event_queue, config) signatures
        config = {}
        event_queue = None
        if len(args) == 1:
            if isinstance(args[0], dict):
                config = args[0]
            else:
                event_queue = args[0]
        elif len(args) == 2:
            event_queue = args[0]
            config = args[1]
            
        self.config = kwargs.get("config", config or {})
        self.event_queue = kwargs.get("event_queue", event_queue)
        
        self.poll_interval_ms: int = self.config.get("tick_poll_interval_ms", 100)
        self.tick_buffer: deque = deque(maxlen=self.config.get("realtime_tick_buffer_size", 10_000))
        self.running = True

        # Candle builders for each timeframe
        max_history = self.config.get("realtime_candle_history", 500)
        self.candle_builders: Dict[str, CandleBuilder] = {
            tf: CandleBuilder(tf, max_history) for tf in _TF_PERIODS
        }

        # Latest bid/ask
        self.latest_bid: float = 0.0
        self.latest_ask: float = 0.0
        self.latest_timestamp: Optional[datetime] = None

        # Callbacks
        self.on_tick_callback: Optional[Callable] = None
        self.on_candle_close_callback: Optional[Callable] = None

        # Internal state for polling
        self._last_tick_time_msc: Optional[int] = None
        self._mt5 = None
        self._tick_count: int = 0

        self.latest_imbalance = {
            "imbalance_10s": 0.0,
            "imbalance_60s": 0.0,
            "imbalance_300s": 0.0,
            "confluence": "neutral"
        }

    @property
    def tick_buffer_list(self) -> list:
        """Expose tick_buffer as a list."""
        return list(self.tick_buffer)

    def _calculate_order_imbalance(self, ticks: list) -> dict:
        """Calculate order imbalance across 10s, 60s, and 300s windows."""
        if not ticks or len(ticks) < 2:
            return {
                "imbalance_10s": 0.0,
                "imbalance_60s": 0.0,
                "imbalance_300s": 0.0,
                "confluence": "neutral"
            }
        
        latest_time = ticks[-1]['time']
        
        t_10s = [t for t in ticks if (latest_time - t['time']).total_seconds() <= 10.0]
        t_60s = [t for t in ticks if (latest_time - t['time']).total_seconds() <= 60.0]
        t_300s = [t for t in ticks if (latest_time - t['time']).total_seconds() <= 300.0]
        
        def calc_imb(window_ticks):
            if len(window_ticks) < 2:
                return 0.0
            buy_val = sum(window_ticks[i]['volume'] * abs(window_ticks[i]['mid'] - window_ticks[i-1]['mid']) for i in range(1, len(window_ticks)) if window_ticks[i]['mid'] > window_ticks[i-1]['mid'])
            sell_val = sum(window_ticks[i]['volume'] * abs(window_ticks[i]['mid'] - window_ticks[i-1]['mid']) for i in range(1, len(window_ticks)) if window_ticks[i]['mid'] < window_ticks[i-1]['mid'])
            total = buy_val + sell_val
            return (buy_val - sell_val) / total if total > 0 else 0.0
        
        i10 = calc_imb(t_10s)
        i60 = calc_imb(t_60s)
        i300 = calc_imb(t_300s)
        
        confluence = "neutral"
        if i300 > 0.25:
            confluence = "bullish"
        elif i300 < -0.25:
            confluence = "bearish"
            
        return {
            "imbalance_10s": float(i10),
            "imbalance_60s": float(i60),
            "imbalance_300s": float(i300),
            "confluence": confluence
        }

    def _init_mt5(self) -> bool:
        """Lazy-load and initialize MT5."""
        try:
            import MetaTrader5 as mt5
            self._mt5 = mt5
            if not mt5.initialize():
                logger.error("TickEngine: MT5 init failed: %s", mt5.last_error())
                return False
            # Ensure symbol visible
            info = mt5.symbol_info(self.symbol)
            if info is None:
                logger.error("TickEngine: Symbol %s not found", self.symbol)
                return False
            if not info.visible:
                mt5.symbol_select(self.symbol, True)
            logger.info("TickEngine: MT5 connected, symbol %s ready", self.symbol)

            # Pre-seed active candles from MT5
            self._preseed_active_candles()

            # Initialize last tick time to current tick using millisecond timestamp
            tick = mt5.symbol_info_tick(self.symbol)
            if tick is not None:
                self._last_tick_time_msc = int(tick.time_msc)
                logger.info("TickEngine: Set last tick time msc to %d", self._last_tick_time_msc)
                self.latest_bid = float(tick.bid)
                self.latest_ask = float(tick.ask)
                self.latest_timestamp = datetime.fromtimestamp(tick.time)
            else:
                self._last_tick_time_msc = int(time.time() * 1000)
                # Seed from last closed M1 rate if tick is None (e.g. weekend)
                rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M1, 0, 1)
                if rates is not None and len(rates) > 0:
                    self.latest_bid = float(rates[0]['close'])
                    self.latest_ask = self.latest_bid + (0.012 if "JPY" in self.symbol.upper() else 0.00012)
                    self.latest_timestamp = datetime.fromtimestamp(rates[0]['time'])
                    logger.info("TickEngine: Seeded initial bid=%.5f, ask=%.5f from M1 rate", self.latest_bid, self.latest_ask)

            return True
        except ImportError:
            logger.error("TickEngine: MetaTrader5 package not installed")
            return False

    def _preseed_active_candles(self) -> None:
        """Pre-seed active incomplete candles from MT5."""
        if self._mt5 is None:
            return

        logger.info("TickEngine: Pre-seeding active candles from MT5...")
        for tf, builder in self.candle_builders.items():
            try:
                tf_constant = getattr(self._mt5, f"TIMEFRAME_{tf}", None)
                if tf_constant is None:
                    continue

                # Fetch index 0 (active incomplete bar)
                rates = self._mt5.copy_rates_from_pos(self.symbol, tf_constant, 0, 1)
                if rates is not None and len(rates) > 0:
                    rate = rates[0]
                    open_time = datetime.fromtimestamp(rate['time'], tz=timezone.utc).replace(tzinfo=None)
                    open_val = float(rate['open'])
                    high_val = float(rate['high'])
                    low_val = float(rate['low'])
                    close_val = float(rate['close'])
                    volume_val = int(rate['tick_volume'])

                    builder.current = LiveCandle(
                        timeframe=tf,
                        open_time=open_time,
                        open=open_val,
                        high=high_val,
                        low=low_val,
                        close=close_val,
                        volume=volume_val,
                        is_closed=False
                    )
                    logger.info(
                        "TickEngine: Pre-seeded %s candle. Time: %s, OHLC: (%.5f, %.5f, %.5f, %.5f), Vol: %d",
                        tf, open_time, open_val, high_val, low_val, close_val, volume_val
                    )
            except Exception as e:
                logger.error("TickEngine: Failed to pre-seed active candle for %s: %s", tf, e)

    def _poll_ticks(self) -> list:
        """Fetch new ticks since last known tick time."""
        if self._mt5 is None:
            return []
        try:
            if self._last_tick_time_msc is None:
                # First poll — get last 100 ticks
                from_time = datetime.utcnow() - timedelta(seconds=10)
                ticks = self._mt5.copy_ticks_from(
                    self.symbol, from_time, 100, self._mt5.COPY_TICKS_ALL
                )
            else:
                # Subsequent polls — get ticks since last known using millisecond timestamp converted to seconds
                from_sec = int(self._last_tick_time_msc // 1000)
                ticks = self._mt5.copy_ticks_from(
                    self.symbol, from_sec, 1000, self._mt5.COPY_TICKS_ALL
                )
            if ticks is None or len(ticks) == 0:
                return []

            # Filter out already-seen ticks using millisecond accuracy to support multiple ticks per second
            new_ticks = []
            for t in ticks:
                tick_msc = int(t['time_msc'])
                if self._last_tick_time_msc is None or tick_msc > self._last_tick_time_msc:
                    new_ticks.append(t)

            if new_ticks:
                self._last_tick_time_msc = int(new_ticks[-1]['time_msc'])

            return new_ticks
        except Exception as e:
            logger.warning("TickEngine poll error: %s", e)
            return []

    def _process_tick(self, tick) -> None:
        """Update bid/ask, feed candle builders, invoke callbacks."""
        bid = float(tick['bid'])
        ask = float(tick['ask'])
        
        # Handle dict or numpy structured array safely
        if isinstance(tick, dict):
            volume = int(tick.get('volume', 1))
            time_msc = tick.get('time_msc', int(time.time() * 1000))
        else:
            volume = int(tick['volume']) if 'volume' in tick.dtype.names else 1
            time_msc = tick['time_msc']
            
        # Use time_msc to preserve millisecond precision in the timestamp!
        timestamp = datetime.fromtimestamp(time_msc / 1000.0, tz=timezone.utc).replace(tzinfo=None)
        mid_price = (bid + ask) / 2.0

        self.latest_bid = bid
        self.latest_ask = ask
        self.latest_timestamp = timestamp
        self._tick_count += 1

        # Store in buffer
        self.tick_buffer.append({
            'time': timestamp, 'bid': bid, 'ask': ask,
            'mid': mid_price, 'volume': volume
        })

        # Calculate and store latest imbalance
        result = self._calculate_order_imbalance(self.tick_buffer_list)
        self.latest_imbalance = result

        # Invoke tick callback (passing volume as well!)
        if self.on_tick_callback:
            try:
                self.on_tick_callback(bid, ask, timestamp, volume)
            except Exception as e:
                logger.warning("Tick callback error: %s", e)

        # Feed all candle builders
        for tf, builder in self.candle_builders.items():
            closed = builder.feed_tick(bid, volume, timestamp)
            if closed is not None and self.on_candle_close_callback:
                try:
                    self.on_candle_close_callback(closed)
                except Exception as e:
                    logger.warning("Candle close callback error (%s): %s", tf, e)

    def run(self):
        """Main polling loop."""
        logger.info("TickEngine starting for %s (poll interval: %dms)",
                    self.symbol, self.poll_interval_ms)

        if not self._init_mt5():
            logger.error("TickEngine: Cannot start without MT5")
            self.running = False
            return

        from axonai.dataflows.mt5_data import get_broker_tz_offset

        while self.running:
            # Check if market is closed (weekend in broker local time)
            offset_hours = get_broker_tz_offset(self.symbol)
            broker_now = datetime.utcnow() + timedelta(hours=offset_hours)
            
            # If weekend, simulate live price action so the dashboard chart remains active and updated!
            if broker_now.weekday() in (5, 6):
                last_price = self.mid_price if self.latest_bid > 0.0 else 1.16110
                import random
                pip_unit = 0.01 if "JPY" in self.symbol.upper() else 0.0001
                change = random.uniform(-0.15, 0.15) * pip_unit
                new_mid = last_price + change
                spread_val = 1.2 * pip_unit
                mock_bid = new_mid - spread_val / 2.0
                mock_ask = new_mid + spread_val / 2.0
                
                mock_tick = {
                    'bid': mock_bid,
                    'ask': mock_ask,
                    'volume': random.randint(1, 4),
                    'time_msc': int(time.time() * 1000)
                }
                self._process_tick(mock_tick)
                # Sleep a dynamic tick rate (e.g. between 500ms and 1.5s) on weekends to keep it natural
                time.sleep(random.uniform(0.5, 1.5))
                continue

            new_ticks = self._poll_ticks()
            for tick in new_ticks:
                self._process_tick(tick)
            time.sleep(self.poll_interval_ms / 1000.0)

        logger.info("TickEngine stopped. Total ticks processed: %d", self._tick_count)

    def stop(self):
        """Signal the thread to stop."""
        self.running = False

    @property
    def spread(self) -> float:
        """Current spread in raw price units."""
        return self.latest_ask - self.latest_bid

    @property
    def mid_price(self) -> float:
        """Current mid price."""
        return (self.latest_bid + self.latest_ask) / 2.0
