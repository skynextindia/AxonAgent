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
        epoch = ts.replace(tzinfo=None)
        total_seconds = int(epoch.timestamp())
        period_start_sec = (total_seconds // self.period_seconds) * self.period_seconds
        return datetime.fromtimestamp(period_start_sec)

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

    def __init__(self, symbol: str, config: dict):
        super().__init__(daemon=True, name=f"TickEngine-{symbol}")
        self.symbol = symbol  # MT5 symbol e.g. "EURUSDm"
        self.config = config
        self.poll_interval_ms: int = config.get("tick_poll_interval_ms", 100)
        self.tick_buffer: deque = deque(maxlen=config.get("realtime_tick_buffer_size", 10_000))
        self.running = True

        # Candle builders for each timeframe
        max_history = config.get("realtime_candle_history", 500)
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
        self._last_tick_time: Optional[datetime] = None
        self._mt5 = None
        self._tick_count: int = 0

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
            return True
        except ImportError:
            logger.error("TickEngine: MetaTrader5 package not installed")
            return False

    def _poll_ticks(self) -> list:
        """Fetch new ticks since last known tick time."""
        if self._mt5 is None:
            return []
        try:
            if self._last_tick_time is None:
                # First poll — get last 100 ticks
                from_time = datetime.now() - timedelta(seconds=10)
                ticks = self._mt5.copy_ticks_from(
                    self.symbol, from_time, 100, self._mt5.COPY_TICKS_ALL
                )
            else:
                # Subsequent polls — get ticks since last known
                ticks = self._mt5.copy_ticks_from(
                    self.symbol, self._last_tick_time, 1000, self._mt5.COPY_TICKS_ALL
                )
            if ticks is None or len(ticks) == 0:
                return []

            # Filter out already-seen ticks
            new_ticks = []
            for t in ticks:
                tick_time = datetime.fromtimestamp(t['time'])
                if self._last_tick_time is None or tick_time > self._last_tick_time:
                    new_ticks.append(t)

            if new_ticks:
                last = new_ticks[-1]
                self._last_tick_time = datetime.fromtimestamp(last['time'])

            return new_ticks
        except Exception as e:
            logger.warning("TickEngine poll error: %s", e)
            return []

    def _process_tick(self, tick) -> None:
        """Update bid/ask, feed candle builders, invoke callbacks."""
        bid = float(tick['bid'])
        ask = float(tick['ask'])
        volume = int(tick['volume']) if 'volume' in tick.dtype.names else 1
        timestamp = datetime.fromtimestamp(tick['time'])
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

        # Invoke tick callback
        if self.on_tick_callback:
            try:
                self.on_tick_callback(bid, ask, timestamp)
            except Exception as e:
                logger.warning("Tick callback error: %s", e)

        # Feed all candle builders
        for tf, builder in self.candle_builders.items():
            closed = builder.feed_tick(mid_price, volume, timestamp)
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

        while self.running:
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
