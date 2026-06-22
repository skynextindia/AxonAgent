"""MT5 tick receiver and normalizer."""

import logging
import threading
import time
from typing import Dict, List, Callable, Optional
import MetaTrader5 as mt5

from tick_engine import Tick, TickBuffer, TickProcessor, SignalState

logger = logging.getLogger(__name__)

class TickReceiver:
    """Manages raw tick polling threads and normalized ingestion from MT5."""
    def __init__(self, symbols: List[str] = ["EURUSD", "GBPUSD"], debug: bool = False):
        self.symbols = symbols
        self.debug = debug
        self.buffers: Dict[str, TickBuffer] = {}
        self.processors: Dict[str, TickProcessor] = {}
        self.callbacks: Dict[str, List[Callable[[SignalState], None]]] = {s: [] for s in symbols}
        self._threads: Dict[str, threading.Thread] = {}
        self._running = False
        self._lock = threading.Lock()
        self.tick_counts: Dict[str, int] = {s: 0 for s in symbols}

    def register_callback(self, symbol: str, callback: Callable[[SignalState], None]):
        """Register a callback for processing SignalState updates."""
        if symbol in self.callbacks:
            self.callbacks[symbol].append(callback)

    def _connect_mt5(self) -> bool:
        """Initialize connection to MetaTrader 5."""
        try:
            if not mt5.initialize():
                logger.error("MT5 initialization failed: %s", mt5.last_error())
                return False
            
            import os
            suffix = os.getenv("AXONAI_MT5_SYMBOL_SUFFIX", "m")
            
            resolved_symbols = []
            for symbol in self.symbols:
                # Try raw symbol
                if mt5.symbol_select(symbol, True):
                    resolved_symbols.append(symbol)
                # Try with suffix
                elif mt5.symbol_select(symbol + suffix, True):
                    resolved_symbols.append(symbol + suffix)
                # Try with common suffix formats
                elif mt5.symbol_select(symbol + ".m", True):
                    resolved_symbols.append(symbol + ".m")
                else:
                    logger.error("Failed to select symbol %s (tried with suffix '%s' and '.m')", symbol, suffix)
                    return False
            
            # Map original callbacks to resolved suffix counterparts
            new_callbacks = {}
            for original, resolved in zip(self.symbols, resolved_symbols):
                new_callbacks[resolved] = self.callbacks.get(original, [])
            
            # Update active symbols list to their resolved counterparts
            self.symbols = resolved_symbols
            self.callbacks = new_callbacks
            self.tick_counts = {s: 0 for s in resolved_symbols}
            return True
        except Exception as e:
            logger.error("Exception connecting to MT5: %s", e)
            return False

    def _poll_loop(self, symbol: str):
        """Tight loop polling for symbol ticks."""
        buffer = self.buffers[symbol]
        processor = self.processors[symbol]
        last_time_ms = 0
        reconnect_attempts = 0

        while self._running:
            try:
                raw_tick = mt5.symbol_info_tick(symbol)
                if raw_tick is None:
                    # MT5 might have disconnected or symbol is invalid
                    err = mt5.last_error()
                    logger.error("MT5 poll error for %s: %s", symbol, err)
                    
                    if reconnect_attempts < 3:
                        reconnect_attempts += 1
                        logger.warning("Attempting reconnection %d/3 for %s in 5s...", reconnect_attempts, symbol)
                        time.sleep(5.0)
                        mt5.initialize()
                        continue
                    else:
                        logger.critical("Max reconnection attempts reached for %s", symbol)
                        break

                reconnect_attempts = 0 # reset on successful poll
                time_ms = int(raw_tick.time_msc)

                # Skip duplicates
                if time_ms == last_time_ms:
                    time.sleep(0.001)
                    continue

                last_time_ms = time_ms
                bid = float(raw_tick.bid)
                ask = float(raw_tick.ask)
                last = float(raw_tick.last) if raw_tick.last else bid
                volume = int(raw_tick.volume)

                tick = Tick(
                    bid=bid,
                    ask=ask,
                    last=last,
                    time_ms=time_ms,
                    volume=volume,
                    mid=(bid + ask) / 2.0,
                    spread=ask - bid
                )

                buffer.push(tick)
                signal_state = processor.process(tick)
                
                with self._lock:
                    self.tick_counts[symbol] += 1

                if self.debug:
                    print(f"[{symbol}] Tick received: Mid={tick.mid:.5f}, Spread={tick.spread:.5f}")

                for callback in self.callbacks[symbol]:
                    try:
                        callback(signal_state)
                    except Exception as cb_err:
                        logger.warning("Callback error: %s", cb_err)

            except Exception as e:
                logger.error("Exception in poll loop for %s: %s", symbol, e)
                time.sleep(1.0)

    def start(self):
        """Start receiver threads for all configured symbols."""
        with self._lock:
            if self._running:
                return
            self._running = True

        if not self._connect_mt5():
            raise RuntimeError("Could not establish initial MT5 connection.")

        for symbol in self.symbols:
            self.buffers[symbol] = TickBuffer()
            self.processors[symbol] = TickProcessor()
            t = threading.Thread(target=self._poll_loop, args=(symbol,), daemon=True)
            self._threads[symbol] = t
            t.start()
            logger.info("Started tick polling thread for %s", symbol)

    def stop(self):
        """Stop all polling threads and shutdown MT5 link."""
        with self._lock:
            if not self._running:
                return
            self._running = False

        for symbol, t in self._threads.items():
            t.join(timeout=2.0)
            logger.info("Stopped tick polling thread for %s", symbol)

        try:
            mt5.shutdown()
        except Exception as e:
            logger.warning("Shutdown error: %s", e)
