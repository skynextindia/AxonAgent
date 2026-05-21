# axonai/dataflows/evidence_extractor.py

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import List, Dict, Any, Optional

import pandas as pd
import numpy as np

from axonai.dataflows.mt5_data import (
    mt5_initialize,
    _to_mt5_symbol,
    _ensure_symbol_visible,
    _fetch_bars,
    get_mt5_live_price
)

logger = logging.getLogger(__name__)

@dataclass
class MarketEvidence:
    # Structure (from price data)
    swing_highs: list            # last 3 swing highs with prices and timestamps: [{"price": 1.1234, "time": "2026-05-21 14:00"}]
    swing_lows: list             # last 3 swing lows
    key_levels: list             # SR zones within 20 pips of current price
    trend_direction_h1: str      # "up" / "down" / "sideways"
    trend_direction_h4: str
    
    # Momentum
    rsi_h1: float
    macd_signal_h1: str          # "bullish" / "bearish" / "neutral"
    
    # Candle patterns (last 3 candles)
    recent_patterns: list        # ["pin_bar", "engulfing"] etc
    
    # Session data
    asian_range_high: float
    asian_range_low: float
    london_open_bias: str        # "bullish" / "bearish" / "neutral" based on open vs asian range


def extract_market_evidence(symbol: str = "EURUSD=X") -> MarketEvidence:
    """Extract structured facts from raw MT5 data in pure Python. Fail safe if MT5 is unavailable."""
    # Default fallback
    fallback_evidence = MarketEvidence(
        swing_highs=[],
        swing_lows=[],
        key_levels=[],
        trend_direction_h1="sideways",
        trend_direction_h4="sideways",
        rsi_h1=50.0,
        macd_signal_h1="neutral",
        recent_patterns=[],
        asian_range_high=0.0,
        asian_range_low=0.0,
        london_open_bias="neutral"
    )

    if not mt5_initialize():
        logger.warning("MT5 unavailable, returning default MarketEvidence")
        return fallback_evidence

    try:
        mt5_sym = _to_mt5_symbol(symbol)
        if not _ensure_symbol_visible(mt5_sym):
            logger.warning("Symbol %s not visible, returning default MarketEvidence", mt5_sym)
            return fallback_evidence

        # Fetch H1 and H4 bars
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=20)
        df_h1 = _fetch_bars(mt5_sym, "H1", start_dt, end_dt)
        df_h4 = _fetch_bars(mt5_sym, "H4", end_dt - timedelta(days=60), end_dt)

        if df_h1 is None or len(df_h1) < 40 or df_h4 is None or len(df_h4) < 15:
            logger.warning("Insufficient data fetched, returning default MarketEvidence")
            return fallback_evidence

        pip_multiplier = 0.01 if "JPY" in mt5_sym else 0.0001
        current_price = float(df_h1["Close"].iloc[-1])

        # 1. Swing Highs and Lows (from H1, looking for local extremum of window=2)
        swing_highs = []
        swing_lows = []
        
        # Scan H1 backwards to find recent swing highs and lows
        highs = df_h1["High"].values
        lows = df_h1["Low"].values
        times = df_h1.index

        for i in range(len(df_h1) - 3, 2, -1):
            # Check swing high
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                swing_highs.append({
                    "price": float(highs[i]),
                    "time": times[i].strftime("%Y-%m-%d %H:%M")
                })
                if len(swing_highs) >= 3:
                    break

        for i in range(len(df_h1) - 3, 2, -1):
            # Check swing low
            if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                swing_lows.append({
                    "price": float(lows[i]),
                    "time": times[i].strftime("%Y-%m-%d %H:%M")
                })
                if len(swing_lows) >= 3:
                    break

        # 2. Key levels within 20 pips of current price (S/R zones)
        # Use swing highs and lows from H1 and H4 as potential levels
        all_potential_levels = []
        for sh in swing_highs:
            all_potential_levels.append(sh["price"])
        for sl in swing_lows:
            all_potential_levels.append(sl["price"])
            
        # Also grab some H4 swing highs/lows for higher timeframe key levels
        h4_highs = df_h4["High"].values
        h4_lows = df_h4["Low"].values
        for i in range(len(df_h4) - 3, 2, -1):
            if h4_highs[i] > h4_highs[i-1] and h4_highs[i] > h4_highs[i-2] and h4_highs[i] > h4_highs[i+1] and h4_highs[i] > h4_highs[i+2]:
                all_potential_levels.append(float(h4_highs[i]))
            if h4_lows[i] < h4_lows[i-1] and h4_lows[i] < h4_lows[i-2] and h4_lows[i] < h4_lows[i+1] and h4_lows[i] < h4_lows[i+2]:
                all_potential_levels.append(float(h4_lows[i]))

        # Filter unique levels within 20 pips of current price
        key_levels = []
        pip_20 = 20 * pip_multiplier
        for level in sorted(list(set(all_potential_levels))):
            if abs(level - current_price) <= pip_20:
                key_levels.append(round(level, 5))
                
        # Limit to 5 unique key levels closest to current price
        key_levels = sorted(key_levels, key=lambda x: abs(x - current_price))[:5]

        # 3. Trend Direction H1 and H4
        # Compute EMA20 and EMA50
        def get_trend(df):
            close_series = df["Close"]
            ema20 = close_series.ewm(span=20, adjust=False).mean()
            ema50 = close_series.ewm(span=50, adjust=False).mean()
            latest_close = close_series.iloc[-1]
            latest_ema20 = ema20.iloc[-1]
            latest_ema50 = ema50.iloc[-1]
            
            if latest_close > latest_ema20 and latest_ema20 > latest_ema50:
                return "up"
            elif latest_close < latest_ema20 and latest_ema20 < latest_ema50:
                return "down"
            else:
                return "sideways"

        trend_direction_h1 = get_trend(df_h1)
        trend_direction_h4 = get_trend(df_h4)

        # 4. Momentum (RSI H1 and MACD Signal H1)
        # RSI calculation
        close_delta = df_h1["Close"].diff()
        up = close_delta.clip(lower=0)
        down = -1 * close_delta.clip(upper=0)
        ma_up = up.rolling(window=14).mean()
        ma_down = down.rolling(window=14).mean()
        rs = ma_up / (ma_down + 1e-8)
        rsi = 100 - (100 / (1 + rs))
        rsi_h1 = float(rsi.iloc[-1])

        # MACD calculation (12, 26, 9)
        ema12 = df_h1["Close"].ewm(span=12, adjust=False).mean()
        ema26 = df_h1["Close"].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_diff = macd_line.iloc[-1] - signal_line.iloc[-1]
        
        if macd_diff > 1e-6:
            macd_signal_h1 = "bullish"
        elif macd_diff < -1e-6:
            macd_signal_h1 = "bearish"
        else:
            macd_signal_h1 = "neutral"

        # 5. Candle patterns (last 3 H1 candles)
        recent_patterns = []
        
        for idx in [-1, -2, -3]:
            c_high = df_h1["High"].iloc[idx]
            c_low = df_h1["Low"].iloc[idx]
            c_open = df_h1["Open"].iloc[idx]
            c_close = df_h1["Close"].iloc[idx]
            
            c_range = c_high - c_low + 1e-8
            body = abs(c_close - c_open)
            upper_shadow = c_high - max(c_open, c_close)
            lower_shadow = min(c_open, c_close) - c_low
            
            # Pin Bar definition: body < 30% of range, and one shadow > 60% of range
            if body / c_range < 0.30:
                if upper_shadow / c_range > 0.60 and c_close <= c_open:
                    recent_patterns.append(f"pin_bar_bearish_idx{abs(idx)}")
                elif lower_shadow / c_range > 0.60 and c_close >= c_open:
                    recent_patterns.append(f"pin_bar_bullish_idx{abs(idx)}")
            
            # Engulfing (compare with previous bar)
            if idx > -len(df_h1):
                prev_open = df_h1["Open"].iloc[idx - 1]
                prev_close = df_h1["Close"].iloc[idx - 1]
                prev_body = abs(prev_close - prev_open)
                
                # Bullish Engulfing
                if c_close > c_open and prev_close < prev_open:
                    if c_open <= prev_close and c_close >= prev_open and body > prev_body:
                        recent_patterns.append(f"engulfing_bullish_idx{abs(idx)}")
                # Bearish Engulfing
                elif c_close < c_open and prev_close > prev_open:
                    if c_open >= prev_close and c_close <= prev_open and body > prev_body:
                        recent_patterns.append(f"engulfing_bearish_idx{abs(idx)}")

        # 6. Session data (Asian range and London bias)
        # Asian range UTC hours: [00:00 to 08:00)
        # Find Asian session bars of the last 24 hours
        now_utc = datetime.now(timezone.utc)
        start_search = now_utc - timedelta(hours=24)
        
        # Filter H1 index by UTC hours 0-7
        df_utc = df_h1.copy()
        df_utc.index = df_utc.index.tz_localize('UTC') if df_utc.index.tz is None else df_utc.index.tz_convert('UTC')
        asian_bars = df_utc[(df_utc.index >= start_search) & (df_utc.index.hour < 8)]
        
        if not asian_bars.empty:
            asian_range_high = float(asian_bars["High"].max())
            asian_range_low = float(asian_bars["Low"].min())
        else:
            # Fallback to general range of the last 8 hours of prior session
            asian_range_high = float(df_h1["High"].iloc[-12:-4].max())
            asian_range_low = float(df_h1["Low"].iloc[-12:-4].min())

        # London open bias based on the H1 close at 08:00 UTC (or the latest close) vs the Asian range
        # Find the close price at UTC hour 8, or if not found, use latest H1 close
        london_bars = df_utc[df_utc.index.hour == 8]
        if not london_bars.empty:
            london_close = float(london_bars["Close"].iloc[-1])
        else:
            london_close = current_price

        if london_close > asian_range_high:
            london_open_bias = "bullish"
        elif london_close < asian_range_low:
            london_open_bias = "bearish"
        else:
            london_open_bias = "neutral"

        return MarketEvidence(
            swing_highs=swing_highs,
            swing_lows=swing_lows,
            key_levels=key_levels,
            trend_direction_h1=trend_direction_h1,
            trend_direction_h4=trend_direction_h4,
            rsi_h1=rsi_h1,
            macd_signal_h1=macd_signal_h1,
            recent_patterns=recent_patterns,
            asian_range_high=asian_range_high,
            asian_range_low=asian_range_low,
            london_open_bias=london_open_bias
        )

    except Exception as e:
        logger.error("Error extracting market evidence: %s", e, exc_info=True)
        return fallback_evidence


if __name__ == "__main__":
    print("Testing MarketEvidence extractor...")
    evidence = extract_market_evidence("EURUSD=X")
    import pprint
    pprint.pprint(evidence)
