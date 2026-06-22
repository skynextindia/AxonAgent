# axonai/world_state.py

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging
import math
from typing import Dict, Optional, Tuple

import pandas as pd
import numpy as np

from axonai.dataflows.mt5_data import (
    mt5_initialize,
    _to_mt5_symbol,
    _ensure_symbol_visible,
    _fetch_bars,
    get_mt5_live_price,
    get_mt5_atr
)

logger = logging.getLogger(__name__)

@dataclass
class WorldState:
    # Regime probabilities
    regime_scores: Dict[str, float]  # {"trending": 0.71, "ranging": 0.11, "breakout": 0.62, "compression": 0.04, "panic": 0.02}
    dominant_regime: str             # max(regime_scores)
    regime_confidence: float         # max score value, 0-1

    # Volatility
    volatility_regime: str           # "low" / "medium" / "high"
    atr_14_h1: float                 # pips

    # Session
    session: str                     # "asian" / "london" / "newyork" / "overlap"
    session_quality: float           # 0-1 volume percentile vs 20-day average
    session_penalty: float           # 1.0 normal, 0.25 asian, 0.5 rollover
    hours_since_london_open: float

    # Spread
    spread_pips: float
    spread_safe: bool                # spread < 0.3 * ATR

    # Currency strength — composite
    eur_strength: float              # weighted across EURUSD EURJPY EURGBP
    usd_strength: float              # weighted across EURUSD GBPUSD USDJPY

    # Belief score
    belief_score: float              # (regime_confidence*0.35) + (session_quality*0.25) + (trend_score*0.20) + (spread_score*0.20)
    should_run_graph: bool           # belief_score * session_penalty > 0.60
    abort_reason: str                # populated if should_run_graph=False


def _compute_percentile(value: float, series: pd.Series) -> float:
    """Compute percentile score (0-100) of value within series."""
    if series.empty:
        return 50.0
    return float((series < value).sum() / len(series) * 100.0)


def _get_pair_momentum_and_volume(symbol: str) -> Tuple[float, float]:
    """Fetch 3-bar momentum and volume weight for currency strength calculation."""
    try:
        mt5_sym = _to_mt5_symbol(symbol)
        _ensure_symbol_visible(mt5_sym)
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=5)
        df = _fetch_bars(mt5_sym, "H1", start_dt, end_dt)
        if df is None or len(df) < 5:
            return 0.0, 1.0
        
        close_now = df["Close"].iloc[-1]
        close_3 = df["Close"].iloc[-4]
        momentum = (close_now - close_3) / (close_3 + 1e-8)
        
        vol_now = df["Volume"].iloc[-1]
        vol_avg = df["Volume"].rolling(20).mean().iloc[-1]
        vol_weight = vol_now / (vol_avg + 1e-8)
        
        return float(momentum), float(vol_weight)
    except Exception as e:
        logger.warning("Error fetching currency stats for %s: %s", symbol, e)
        return 0.0, 1.0


def build_world_state(symbol: str = "EURUSD=X") -> WorldState:
    """Factory function to build WorldState in pure Python using MT5 data."""
    from datetime import timedelta
    
    # Pre-flight availability check
    if not mt5_initialize():
        return WorldState(
            regime_scores={"trending": 0.0, "ranging": 1.0, "breakout": 0.0, "compression": 0.0, "panic": 0.0},
            dominant_regime="ranging",
            regime_confidence=1.0,
            volatility_regime="low",
            atr_14_h1=0.0010,
            session="asian",
            session_quality=0.0,
            session_penalty=0.25,
            hours_since_london_open=0.0,
            spread_pips=10.0,
            spread_safe=False,
            eur_strength=0.0,
            usd_strength=0.0,
            belief_score=0.0,
            should_run_graph=False,
            abort_reason="mt5_unavailable"
        )

    try:
        mt5_sym = _to_mt5_symbol(symbol)
        if not _ensure_symbol_visible(mt5_sym):
            raise RuntimeError(f"Symbol {mt5_sym} not visible in Market Watch")

        # 1. Fetch live ticks (spread)
        live_tick = get_mt5_live_price(symbol)
        if live_tick is None:
            raise RuntimeError(f"Failed to fetch live ticks for {mt5_sym}")
        bid, ask, last = live_tick
        
        # In FX, pips depend on decimals (typically 5 decimals for major pairs -> 1 pip = 0.0001)
        # For EURUSD, 1 pip = 0.0001, so spread in pips = (ask - bid) / 0.0001
        is_jpy = "JPY" in mt5_sym
        pip_multiplier = 0.01 if is_jpy else 0.0001
        spread_pips = float((ask - bid) / pip_multiplier)

        # 2. Fetch H1 bars for indicators/regimes (last 20 days is approx 480 hours)
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=20)
        df_h1 = _fetch_bars(mt5_sym, "H1", start_dt, end_dt)
        df_h4 = _fetch_bars(mt5_sym, "H4", end_dt - timedelta(days=60), end_dt)

        if df_h1 is None or len(df_h1) < 40 or df_h4 is None or len(df_h4) < 10:
            raise RuntimeError("Insufficient bar data to build WorldState")

        # Calculate H1 ATR(14)
        high = df_h1["High"]
        low = df_h1["Low"]
        close = df_h1["Close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr_series = tr.rolling(window=14).mean()
        
        latest_atr = float(atr_series.iloc[-1])
        latest_atr_pips = latest_atr / pip_multiplier
        spread_safe = spread_pips < (0.3 * latest_atr_pips)

        # Volatility regime
        atr_pct = _compute_percentile(latest_atr, atr_series.dropna())
        if atr_pct > 70.0:
            volatility_regime = "high"
        elif atr_pct < 30.0:
            volatility_regime = "low"
        else:
            volatility_regime = "medium"

        # 3. Session and Timings (UTC-based)
        now_utc = datetime.now(timezone.utc)
        utc_hour = now_utc.hour + now_utc.minute / 60.0
        from zoneinfo import ZoneInfo
        ldn_tz = ZoneInfo("Europe/London")
        ny_tz = ZoneInfo("America/New_York")
        
        dt_ldn = now_utc.astimezone(ldn_tz)
        ldn_open_local = datetime(dt_ldn.year, dt_ldn.month, dt_ldn.day, 8, 0, tzinfo=ldn_tz)
        ldn_close_local = datetime(dt_ldn.year, dt_ldn.month, dt_ldn.day, 16, 0, tzinfo=ldn_tz)
        ldn_open_utc = ldn_open_local.astimezone(timezone.utc)
        ldn_close_utc = ldn_close_local.astimezone(timezone.utc)
        
        dt_ny = now_utc.astimezone(ny_tz)
        ny_open_local = datetime(dt_ny.year, dt_ny.month, dt_ny.day, 8, 0, tzinfo=ny_tz)
        ny_close_local = datetime(dt_ny.year, dt_ny.month, dt_ny.day, 14, 0, tzinfo=ny_tz)
        ny_open_utc = ny_open_local.astimezone(timezone.utc)
        ny_close_utc = ny_close_local.astimezone(timezone.utc)
        
        ldn_open = ldn_open_utc.hour + ldn_open_utc.minute / 60.0
        ldn_close = ldn_close_utc.hour + ldn_close_utc.minute / 60.0
        ny_open = ny_open_utc.hour + ny_open_utc.minute / 60.0
        ny_close = ny_close_utc.hour + ny_close_utc.minute / 60.0
        
        # Session classification based on dynamic hours
        if ny_open <= utc_hour < ldn_close:
            session = "overlap"
            session_penalty = 1.0
            hours_since_london_open = utc_hour - ldn_open
        elif ldn_open <= utc_hour < ny_open:
            session = "london"
            session_penalty = 1.0
            hours_since_london_open = utc_hour - ldn_open
        elif ldn_close <= utc_hour < ny_close:
            session = "newyork"
            session_penalty = 1.0
            hours_since_london_open = utc_hour - ldn_open
        elif ny_close <= utc_hour < (ny_close + 1.0):
            session = "rollover"
            session_penalty = 0.5
            hours_since_london_open = (utc_hour - ldn_open) if utc_hour >= ldn_open else (utc_hour + 24.0 - ldn_open)
        else:
            session = "asian"
            session_penalty = 0.25
            hours_since_london_open = (utc_hour - ldn_open) if utc_hour >= ldn_open else (utc_hour + 24.0 - ldn_open)

        # Session Quality (volume vs 20-day H1 volume)
        latest_volume = float(df_h1["Volume"].iloc[-1])
        vol_series = df_h1["Volume"].rolling(20).mean()
        session_quality = min(latest_volume / (vol_series.iloc[-1] + 1e-8), 1.0)

        # 4. soft scores for regimes
        # Panic score
        recent_move = abs(close.iloc[-1] - close.iloc[-5])
        panic_score = min(atr_pct / 100.0, 1.0) * min(recent_move / (3.0 * latest_atr + 1e-8), 1.0)

        # Breakout score
        high_20_prev = df_h1["High"].shift(1).rolling(20).max().iloc[-1]
        low_20_prev = df_h1["Low"].shift(1).rolling(20).min().iloc[-1]
        dist_high = abs(close.iloc[-1] - high_20_prev)
        dist_low = abs(close.iloc[-1] - low_20_prev)
        breakout_proximity = 1.0 - min(min(dist_high, dist_low) / (latest_atr + 1e-8), 1.0)
        volume_vs_avg = latest_volume / (df_h1["Volume"].rolling(20).mean().iloc[-1] + 1e-8)
        volume_vs_avg_score = min(volume_vs_avg / 1.5, 1.0)
        breakout_score = breakout_proximity * volume_vs_avg_score

        # Trending score
        # Compute EMA20 on H1 and H4
        ema20_h1 = df_h1["Close"].ewm(span=20, adjust=False).mean()
        ema20_h4 = df_h4["Close"].ewm(span=20, adjust=False).mean()
        
        # Check alignment direction
        h1_dir = 1.0 if (ema20_h1.iloc[-1] > ema20_h1.iloc[-2]) else -1.0
        h4_dir = 1.0 if (ema20_h4.iloc[-1] > ema20_h4.iloc[-2]) else -1.0
        ema_alignment_score = 1.0 if (h1_dir == h4_dir) else 0.2
        
        momentum_consistency = abs(close.iloc[-1] - close.iloc[-11]) / (10 * latest_atr + 1e-8)
        momentum_consistency_score = min(momentum_consistency, 1.0)
        trending_score = ema_alignment_score * momentum_consistency_score

        # Compression score (standard Bollinger Band width calculation)
        sma_20 = df_h1["Close"].rolling(20).mean()
        std_20 = df_h1["Close"].rolling(20).std()
        bb_width_series = (4.0 * std_20) / (sma_20 + 1e-8)
        latest_bb_width = bb_width_series.iloc[-1]
        bb_width_pct = _compute_percentile(latest_bb_width, bb_width_series.dropna())
        compression_score = (1.0 - bb_width_pct / 100.0) * (1.0 - atr_pct / 100.0)

        # Ranging score
        ranging_score = float(np.clip(1.0 - max(trending_score, breakout_score, panic_score), 0.0, 1.0))

        regime_scores = {
            "trending": float(trending_score),
            "ranging": ranging_score,
            "breakout": float(breakout_score),
            "compression": float(compression_score),
            "panic": float(panic_score)
        }
        dominant_regime = max(regime_scores, key=regime_scores.get)
        regime_confidence = regime_scores[dominant_regime]

        # 5. Composite Currency strength
        # EUR: EURUSD, EURJPY, EURGBP
        eurusd_mom, eurusd_vol = _get_pair_momentum_and_volume("EURUSD=X")
        eurjpy_mom, eurjpy_vol = _get_pair_momentum_and_volume("EURJPY=X")
        eurgbp_mom, eurgbp_vol = _get_pair_momentum_and_volume("EURGBP=X")
        
        # If cross-pair data is unavailable (meaning EURJPY and EURGBP momentum are 0.0), compute simplified proxy:
        if eurjpy_mom == 0.0 and eurgbp_mom == 0.0 and len(df_h1) >= 4:
            closes = df_h1["Close"].values
            r1 = (closes[-1] - closes[-2]) / (closes[-2] + 1e-8)
            r2 = (closes[-2] - closes[-3]) / (closes[-3] + 1e-8)
            r3 = (closes[-3] - closes[-4]) / (closes[-4] + 1e-8)
            eur_strength = float(np.clip((r1 + r2 + r3) * 100, -1.0, 1.0))
            usd_strength = -eur_strength
        else:
            eur_strength = float(np.mean([
                eurusd_mom * eurusd_vol,
                eurjpy_mom * eurjpy_vol,
                eurgbp_mom * eurgbp_vol
            ]))
            eur_strength = float(np.clip(eur_strength * 100, -1.0, 1.0)) # scale to -1..+1

            # USD: EURUSD (inverted), GBPUSD (inverted), USDJPY
            gbpusd_mom, gbpusd_vol = _get_pair_momentum_and_volume("GBPUSD=X")
            usdjpy_mom, usdjpy_vol = _get_pair_momentum_and_volume("USDJPY=X")
            
            usd_strength = float(np.mean([
                -eurusd_mom * eurusd_vol,
                -gbpusd_mom * gbpusd_vol,
                usdjpy_mom * usdjpy_vol
            ]))
            usd_strength = float(np.clip(usd_strength * 100, -1.0, 1.0))

        # 6. Belief gating calculations
        trend_score = regime_scores["trending"]
        spread_score = 1.0 if spread_safe else float(np.clip(1.0 - (spread_pips / (3.0 * latest_atr_pips + 1e-8)), 0.0, 1.0))
        
        belief_score = (regime_confidence * 0.35) + (session_quality * 0.25) + (trend_score * 0.20) + (spread_score * 0.20)
        
        gated_score = belief_score * session_penalty
        should_run_graph = gated_score > 0.60
        
        abort_reason = ""
        if not should_run_graph:
            if gated_score <= 0.60:
                reasons = []
                if belief_score <= 0.60:
                    reasons.append("low_conviction")
                if session_penalty < 1.0:
                    reasons.append(f"{session}_session")
                if not spread_safe:
                    reasons.append("wide_spread")
                abort_reason = "|".join(reasons) if reasons else "low_conviction"

        return WorldState(
            regime_scores=regime_scores,
            dominant_regime=dominant_regime,
            regime_confidence=regime_confidence,
            volatility_regime=volatility_regime,
            atr_14_h1=float(latest_atr),
            session=session,
            session_quality=session_quality,
            session_penalty=session_penalty,
            hours_since_london_open=hours_since_london_open,
            spread_pips=spread_pips,
            spread_safe=spread_safe,
            eur_strength=eur_strength,
            usd_strength=usd_strength,
            belief_score=belief_score,
            should_run_graph=should_run_graph,
            abort_reason=abort_reason
        )

    except Exception as e:
        logger.error("Error building WorldState: %s", e, exc_info=True)
        return WorldState(
            regime_scores={"trending": 0.0, "ranging": 1.0, "breakout": 0.0, "compression": 0.0, "panic": 0.0},
            dominant_regime="ranging",
            regime_confidence=1.0,
            volatility_regime="low",
            atr_14_h1=0.0010,
            session="asian",
            session_quality=0.0,
            session_penalty=0.25,
            hours_since_london_open=0.0,
            spread_pips=10.0,
            spread_safe=False,
            eur_strength=0.0,
            usd_strength=0.0,
            belief_score=0.0,
            should_run_graph=False,
            abort_reason=f"error_{str(e).replace(' ', '_')}"
        )


if __name__ == "__main__":
    # Standalone Test
    print("Testing WorldState engine for EURUSDm...")
    state = build_world_state("EURUSD=X")
    print(f"WorldState Data:")
    print(f"  Dominant Regime: {state.dominant_regime} (confidence: {state.regime_confidence:.2f})")
    print(f"  Regime Scores: {state.regime_scores}")
    print(f"  Volatility Regime: {state.volatility_regime} (ATR H1: {state.atr_14_h1:.5f})")
    print(f"  Session: {state.session} (quality: {state.session_quality:.2f}, penalty: {state.session_penalty:.2f})")
    print(f"  Hours since London Open: {state.hours_since_london_open:.2f}")
    print(f"  Spread: {state.spread_pips:.2f} pips (safe: {state.spread_safe})")
    print(f"  EUR Strength: {state.eur_strength:+.2f}")
    print(f"  USD Strength: {state.usd_strength:+.2f}")
    print(f"  Belief Score: {state.belief_score:.2f}")
    print(f"  Should Run Graph: {state.should_run_graph}")
    print(f"  Abort Reason: '{state.abort_reason}'")
