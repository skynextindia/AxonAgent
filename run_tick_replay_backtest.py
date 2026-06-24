"""Backtest B — Real Tick Replay.

Purpose: edge validation with real market microstructure.
Loads actual ticks and M15 bars from MetaTrader 5 and replays them tick-by-tick.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Dict, Any

import MetaTrader5 as mt5

from axonai.dataflows.mt5_data import mt5_initialize, _to_mt5_symbol, _ensure_symbol_visible, _fetch_bars
from axonai.realtime.backtester import BacktestEngine
from axonai.realtime.event_types import LiveCandle

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class TickReplayBacktestEngine(BacktestEngine):
    """Subclass of BacktestEngine that replays real ticks from MT5 instead of synthetic ticks."""

    def load_historical_data(self) -> Tuple[List[LiveCandle], List[Tuple[float, float, datetime]]]:
        mt5_sym = _to_mt5_symbol(self.ticker, self.config)
        _ensure_symbol_visible(mt5_sym)
        
        # Determine date range
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=self.days)
        
        logger.info("TickReplay: Loading real M15 candles for %s from %s to %s", mt5_sym, start_dt, end_dt)
        df_m15 = _fetch_bars(mt5_sym, "M15", start_dt, end_dt)
        if df_m15 is None or df_m15.empty:
            raise ValueError(f"Failed to fetch M15 historical bars for symbol {mt5_sym}")
            
        candles: List[LiveCandle] = []
        for t, row in df_m15.iterrows():
            candles.append(LiveCandle(
                timeframe="M15",
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row["Volume"]),
                open_time=t,
                is_closed=True
            ))
            
        logger.info("TickReplay: Loading real tick history for %s from %s to %s", mt5_sym, start_dt, end_dt)
        # Fetch actual tick history
        ticks_data = mt5.copy_ticks_from(mt5_sym, start_dt, 10_000_000, mt5.COPY_TICKS_ALL)
        if ticks_data is None or len(ticks_data) == 0:
            raise ValueError(f"Failed to copy actual ticks from MT5 for {mt5_sym}. Make sure MT5 is running and has history.")
            
        logger.info("TickReplay: Loaded %d real ticks from MT5", len(ticks_data))
        
        ticks: List[Tuple[float, float, datetime]] = []
        for t in ticks_data:
            # convert time_msc to datetime
            dt = datetime.fromtimestamp(t['time_msc'] / 1000.0, tz=timezone.utc).replace(tzinfo=None)
            ticks.append((float(t['bid']), float(t['ask']), dt))
            
        # Ensure they are chronologically ordered
        ticks.sort(key=lambda x: x[2])
        
        return candles, ticks


def main():
    logger.info("Initializing MetaTrader 5 connection...")
    if not mt5_initialize():
        logger.error("MT5 initialization failed. Exiting.")
        sys.exit(1)
        
    symbol = "EURUSD"
    days = 3  # Start with 3 days of real tick data to avoid extremely long runtimes
    logger.info("Creating Tick Replay Backtest Engine for %s (%d days)...", symbol, days)
    
    engine = TickReplayBacktestEngine(ticker=symbol, days=days)
    
    logger.info("Running tick replay backtest...")
    report = engine.run()
    
    # Save statistics
    engine.reversal_model.exit_stats.to_csv("reports/real_tick_exit_stats.csv")
    engine.reversal_model.exit_stats.to_json("reports/real_tick_exit_stats.json")
    
    logger.info("Tick replay backtest finished successfully!")
    logger.info("Performance Report Summary:")
    logger.info("=" * 60)
    logger.info("  Total Trades:    %d", report["total_trades"])
    logger.info("  Wins / Losses:   %d / %d", report["wins"], report["losses"])
    logger.info("  Win Rate:        %.1f%%", report["win_rate_percent"])
    logger.info("  Net P&L:         %+.1f pips", report["net_profit_pips"])
    logger.info("  Profit Factor:   %.2f", report["profit_factor"])
    logger.info("=" * 60)
    logger.info("Exit statistics saved to reports/real_tick_exit_stats.csv and reports/real_tick_exit_stats.json")


if __name__ == "__main__":
    main()
