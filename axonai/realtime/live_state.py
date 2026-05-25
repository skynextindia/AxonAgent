"""Incrementally updated live market state.

Seeds from historical MT5 bars once at startup, then updates
incrementally on each tick and candle close. Never rebuilt from scratch.
"""

from __future__ import annotations

import copy
import logging
import math
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np

from axonai.world_state import WorldState, build_world_state
from axonai.dataflows.evidence_extractor import MarketEvidence, extract_market_evidence
from axonai.realtime.event_types import LiveCandle
from axonai.dataflows.mt5_data import get_broker_tz_offset, _to_mt5_symbol

logger = logging.getLogger(__name__)


class LiveWorldState:
    """Continuously updated WorldState from tick and candle data.

    Update frequency per field:
        - spread: every tick
        - session/penalty: every tick (time-based)
        - ATR, EMA, RSI, MACD, BB: on H1 candle close
        - regime scores: on M15 candle close
        - currency strength: on M5 candle close (cross-pair)
        - belief score: recomputed after any field update
    """

    def __init__(self, symbol: str, config: dict):
        self.symbol = symbol
        self.config = config
        self._state: Optional[WorldState] = None
        self._initialized = False

        # Parse JPY and base/quote symbols dynamically
        sym_clean = symbol.strip().upper().replace("/", "").replace("=X", "")
        if len(sym_clean) >= 6:
            self._base_currency = sym_clean[:3]
            self._quote_currency = sym_clean[3:6]
        else:
            self._base_currency = "EUR"
            self._quote_currency = "USD"
        
        self._is_jpy = self._quote_currency == "JPY"
        self._pip_mult = 0.01 if self._is_jpy else 0.0001

        # Config-driven lengths with defaults
        rsi_len = config.get("indicator_rsi_length", 14)
        bb_len = config.get("indicator_bb_length", 20)

        # Rolling indicator windows (seeded from historical data)
        self._h1_closes: deque = deque(maxlen=100)
        self._h1_highs: deque = deque(maxlen=100)
        self._h1_lows: deque = deque(maxlen=100)
        self._h1_volumes: deque = deque(maxlen=100)
        self._h4_closes: deque = deque(maxlen=60)
        self._tr_window: deque = deque(maxlen=rsi_len)  # True Range for ATR
        self._bb_closes: deque = deque(maxlen=bb_len)   # For Bollinger Bands

        # EMA state (carry forward)
        self._ema20_h1: float = 0.0
        self._ema20_h4: float = 0.0
        self._prev_ema20_h1: float = 0.0
        self._prev_ema20_h4: float = 0.0

        # RSI state
        self._rsi_avg_gain: float = 0.0
        self._rsi_avg_loss: float = 0.0
        self._prev_close_h1: float = 0.0

    def initialize(self):
        """Cold start: build full WorldState from historical bars.
        Called once when daemon starts."""
        logger.info("LiveWorldState: cold-starting from historical data for %s", self.symbol)
        self._state = build_world_state(self.symbol)
        self._initialized = True

        # Seed rolling windows from historical H1/H4 bars
        self._seed_from_history()
        logger.info("LiveWorldState: initialized. regime=%s belief=%.2f session=%s",
                    self._state.dominant_regime, self._state.belief_score, self._state.session)

    def _seed_from_history(self):
        """Populate rolling windows from historical MT5 bars."""
        try:
            from axonai.dataflows.mt5_data import (
                mt5_initialize, _to_mt5_symbol, _ensure_symbol_visible, _fetch_bars
            )
            if not mt5_initialize():
                return

            mt5_sym = _to_mt5_symbol(self.symbol)
            _ensure_symbol_visible(mt5_sym)

            end_dt = datetime.now()
            df_h1 = _fetch_bars(mt5_sym, "H1", end_dt - timedelta(days=20), end_dt)
            df_h4 = _fetch_bars(mt5_sym, "H4", end_dt - timedelta(days=60), end_dt)

            if df_h1 is not None and len(df_h1) >= 40:
                for _, row in df_h1.tail(100).iterrows():
                    self._h1_closes.append(row["Close"])
                    self._h1_highs.append(row["High"])
                    self._h1_lows.append(row["Low"])
                    self._h1_volumes.append(row["Volume"])
                    self._bb_closes.append(row["Close"])

                # Seed ATR (True Range)
                closes = df_h1["Close"].values
                highs = df_h1["High"].values
                lows = df_h1["Low"].values
                for i in range(1, min(15, len(df_h1))):
                    tr = max(
                        highs[-(15-i)] - lows[-(15-i)],
                        abs(highs[-(15-i)] - closes[-(15-i)-1]),
                        abs(lows[-(15-i)] - closes[-(15-i)-1])
                    )
                    self._tr_window.append(tr)

                # Seed EMAs
                ema_series = df_h1["Close"].ewm(span=20, adjust=False).mean()
                self._ema20_h1 = float(ema_series.iloc[-1])
                self._prev_ema20_h1 = float(ema_series.iloc[-2])
                self._prev_close_h1 = float(closes[-1])

                # Seed RSI components
                deltas = np.diff(closes[-15:])
                gains = np.where(deltas > 0, deltas, 0)
                losses = np.where(deltas < 0, -deltas, 0)
                self._rsi_avg_gain = float(np.mean(gains)) if len(gains) > 0 else 0.0
                self._rsi_avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0

            if df_h4 is not None and len(df_h4) >= 15:
                for _, row in df_h4.tail(60).iterrows():
                    self._h4_closes.append(row["Close"])
                ema_h4 = df_h4["Close"].ewm(span=20, adjust=False).mean()
                self._ema20_h4 = float(ema_h4.iloc[-1])
                self._prev_ema20_h4 = float(ema_h4.iloc[-2])

            self._update_currency_strength()

        except Exception as e:
            logger.error("LiveWorldState seed error: %s", e, exc_info=True)

    def on_tick(self, bid: float, ask: float, timestamp: datetime):
        """Update spread and session on every tick. O(1) cost."""
        if not self._initialized or self._state is None:
            return

        # Update spread
        spread_pips = (ask - bid) / self._pip_mult
        self._state.spread_pips = spread_pips

        # Spread safety check
        if self._state.atr_14_h1 > 0:
            atr_pips = self._state.atr_14_h1 / self._pip_mult
            self._state.spread_safe = spread_pips < (0.3 * atr_pips)
        else:
            self._state.spread_safe = False

        # Update session (time-based) using true UTC time adjusted for broker DST/offset
        broker_symbol = _to_mt5_symbol(self.symbol, self.config)
        offset_hours = get_broker_tz_offset(broker_symbol)
        
        if timestamp.tzinfo:
            utc_dt = timestamp.astimezone(timezone.utc)
        else:
            utc_dt = timestamp - timedelta(hours=offset_hours)
            utc_dt = utc_dt.replace(tzinfo=timezone.utc)
        utc_hour = utc_dt.hour + utc_dt.minute / 60.0

        prev_session = self._state.session
        if 13.0 <= utc_hour < 16.0:
            self._state.session = "overlap"
            self._state.session_penalty = 1.0
        elif 8.0 <= utc_hour < 13.0:
            self._state.session = "london"
            self._state.session_penalty = 1.0
        elif 16.0 <= utc_hour < 21.0:
            self._state.session = "newyork"
            self._state.session_penalty = 1.0
        elif 21.0 <= utc_hour < 22.0:
            self._state.session = "rollover"
            self._state.session_penalty = 0.5
        else:
            self._state.session = "asian"
            self._state.session_penalty = 0.25

        self._state.hours_since_london_open = (
            utc_hour - 8.0 if utc_hour >= 8.0 else utc_hour + 16.0
        )

        # Recompute belief and gate
        self._recompute_belief()

        return prev_session != self._state.session  # Return True if session changed

    def on_candle_close(self, candle: LiveCandle):
        """Update indicators when a candle closes."""
        if not self._initialized or self._state is None:
            return

        if candle.timeframe == "H1":
            self._update_h1(candle)
        elif candle.timeframe == "H4":
            self._update_h4(candle)
        elif candle.timeframe == "M15":
            self._update_regimes(candle)
        elif candle.timeframe == "M5":
            self._update_volume(candle)

    def _update_h1(self, candle: LiveCandle):
        """Update ATR, EMA, RSI on H1 candle close."""
        self._h1_closes.append(candle.close)
        self._h1_highs.append(candle.high)
        self._h1_lows.append(candle.low)
        self._h1_volumes.append(candle.volume)
        self._bb_closes.append(candle.close)

        # ATR update
        if self._prev_close_h1 > 0:
            tr = max(
                candle.high - candle.low,
                abs(candle.high - self._prev_close_h1),
                abs(candle.low - self._prev_close_h1)
            )
            self._tr_window.append(tr)
            if len(self._tr_window) >= 14:
                self._state.atr_14_h1 = float(np.mean(list(self._tr_window)))

        # EMA20 H1 update
        k = 2.0 / 21.0  # EMA(20) smoothing factor
        self._prev_ema20_h1 = self._ema20_h1
        self._ema20_h1 = candle.close * k + self._ema20_h1 * (1 - k)

        # RSI update (Wilder's smoothing)
        delta = candle.close - self._prev_close_h1
        gain = max(delta, 0)
        loss = max(-delta, 0)
        self._rsi_avg_gain = (self._rsi_avg_gain * 13 + gain) / 14
        self._rsi_avg_loss = (self._rsi_avg_loss * 13 + loss) / 14

        # Volatility regime from ATR percentile
        if self._state.atr_14_h1 > 0:
            atr_pips = self._state.atr_14_h1 / self._pip_mult
            # Simple threshold-based regime
            if atr_pips > 15:
                self._state.volatility_regime = "high"
            elif atr_pips < 5:
                self._state.volatility_regime = "low"
            else:
                self._state.volatility_regime = "medium"

        self._prev_close_h1 = candle.close
        self._update_currency_strength()
        self._recompute_belief()

    def _update_h4(self, candle: LiveCandle):
        """Update H4 EMA on H4 candle close."""
        self._h4_closes.append(candle.close)
        k = 2.0 / 21.0
        self._prev_ema20_h4 = self._ema20_h4
        self._ema20_h4 = candle.close * k + self._ema20_h4 * (1 - k)

    def _update_regimes(self, candle: LiveCandle):
        """Recompute regime scores on M15 candle close."""
        if len(self._h1_closes) < 20:
            return

        closes = list(self._h1_closes)

        # Trending score: EMA alignment
        h1_dir = 1.0 if self._ema20_h1 > self._prev_ema20_h1 else -1.0
        h4_dir = 1.0 if self._ema20_h4 > self._prev_ema20_h4 else -1.0
        ema_alignment = 1.0 if h1_dir == h4_dir else 0.2

        atr = self._state.atr_14_h1 if self._state.atr_14_h1 > 0 else 1e-8
        momentum = abs(closes[-1] - closes[-min(11, len(closes))]) / (10 * atr + 1e-8)
        trending_score = ema_alignment * min(momentum, 1.0)

        # Breakout score
        highs = list(self._h1_highs)
        lows = list(self._h1_lows)
        if len(highs) >= 20:
            high_20 = max(highs[-21:-1]) if len(highs) > 21 else max(highs[:-1])
            low_20 = min(lows[-21:-1]) if len(lows) > 21 else min(lows[:-1])
            dist = min(abs(closes[-1] - high_20), abs(closes[-1] - low_20))
            breakout_proximity = 1.0 - min(dist / (atr + 1e-8), 1.0)
        else:
            breakout_proximity = 0.0

        vols = list(self._h1_volumes)
        vol_ratio = vols[-1] / (np.mean(vols[-20:]) + 1e-8) if len(vols) >= 20 else 1.0
        breakout_score = breakout_proximity * min(vol_ratio / 1.5, 1.0)

        # Compression score (Bollinger Band width)
        bb = list(self._bb_closes)
        if len(bb) >= 20:
            sma = np.mean(bb[-20:])
            std = np.std(bb[-20:])
            bb_width = (4.0 * std) / (sma + 1e-8)
            # Lower width = higher compression
            compression_score = max(0, 1.0 - bb_width * 100)
        else:
            compression_score = 0.0

        # Panic score
        recent_move = abs(closes[-1] - closes[-min(6, len(closes))])
        panic_score = min(recent_move / (3.0 * atr + 1e-8), 1.0)

        # Ranging score
        ranging_score = float(np.clip(1.0 - max(trending_score, breakout_score, panic_score), 0.0, 1.0))

        self._state.regime_scores = {
            "trending": float(trending_score),
            "ranging": ranging_score,
            "breakout": float(breakout_score),
            "compression": float(compression_score),
            "panic": float(panic_score),
        }
        self._state.dominant_regime = max(self._state.regime_scores, key=self._state.regime_scores.get)
        self._state.regime_confidence = self._state.regime_scores[self._state.dominant_regime]
        self._recompute_belief()

    def _update_volume(self, candle: LiveCandle):
        """Lightweight volume update on M5."""
        # Session quality update
        if len(self._h1_volumes) >= 20:
            avg_vol = np.mean(list(self._h1_volumes)[-20:])
            self._state.session_quality = min(candle.volume / (avg_vol + 1e-8), 1.0)

    def _recompute_belief(self):
        """Recompute belief score and gate decision."""
        if self._state is None:
            return

        trend_score = self._state.regime_scores.get("trending", 0.0)
        atr_pips = self._state.atr_14_h1 / self._pip_mult if self._state.atr_14_h1 > 0 else 0
        spread_score = (
            1.0 if self._state.spread_safe
            else float(np.clip(1.0 - (self._state.spread_pips / (3.0 * atr_pips + 1e-8)), 0.0, 1.0))
        )

        self._state.belief_score = (
            self._state.regime_confidence * 0.35
            + self._state.session_quality * 0.25
            + trend_score * 0.20
            + spread_score * 0.20
        )

        gated = self._state.belief_score * self._state.session_penalty
        self._state.should_run_graph = gated > 0.60

        if not self._state.should_run_graph:
            reasons = []
            if self._state.belief_score <= 0.60:
                reasons.append("low_conviction")
            if self._state.session_penalty < 1.0:
                reasons.append(f"{self._state.session}_session")
            if not self._state.spread_safe:
                reasons.append("wide_spread")
            self._state.abort_reason = "|".join(reasons) if reasons else "low_conviction"
        else:
            self._state.abort_reason = ""

    def _update_currency_strength(self):
        """Update base and quote currency strength dynamically using a single-pair momentum proxy."""
        if self._state is None:
            return
        if len(self._h1_closes) >= 4:
            closes = list(self._h1_closes)
            r1 = (closes[-1] - closes[-2]) / (closes[-2] + 1e-8)
            r2 = (closes[-2] - closes[-3]) / (closes[-3] + 1e-8)
            r3 = (closes[-3] - closes[-4]) / (closes[-4] + 1e-8)
            base_strength = float(np.clip((r1 + r2 + r3) * 100, -1.0, 1.0))
            quote_strength = -base_strength
            
            # Map dynamic strengths to correct state attributes for backward-compatibility
            self._state.eur_strength = base_strength
            self._state.usd_strength = quote_strength

    def snapshot(self) -> WorldState:
        """Return a frozen copy of the current state for graph invocation."""
        if self._state is None:
            return build_world_state(self.symbol)
        return copy.deepcopy(self._state)

    @property
    def current_rsi(self) -> float:
        """Current RSI value."""
        if self._rsi_avg_loss == 0:
            return 100.0
        rs = self._rsi_avg_gain / (self._rsi_avg_loss + 1e-8)
        return 100.0 - (100.0 / (1.0 + rs))

    @property
    def is_initialized(self) -> bool:
        return self._initialized


class LiveMarketEvidence:
    """Incrementally maintained swing highs/lows, key levels, and patterns."""

    def __init__(self, symbol: str, config: Optional[dict] = None):
        self.symbol = symbol
        self.config = config or {}
        self._evidence: Optional[MarketEvidence] = None
        self._initialized = False
        
        # Parse quote dynamically for JPY pairs
        sym_clean = symbol.strip().upper().replace("/", "").replace("=X", "")
        is_jpy = sym_clean.endswith("JPY") or (len(sym_clean) >= 6 and sym_clean[3:6] == "JPY")
        self._pip_mult = 0.01 if is_jpy else 0.0001

        # Rolling candle history for structural detection
        self._m15_candles: deque[LiveCandle] = deque(maxlen=100)
        self._h1_candles: deque[LiveCandle] = deque(maxlen=100)
        self._h4_candles: deque[LiveCandle] = deque(maxlen=100)


    def initialize(self):
        """Cold start from historical bars via extract_market_evidence()."""
        logger.info("LiveMarketEvidence: cold-starting for %s", self.symbol)
        self._evidence = extract_market_evidence(self.symbol)
        self._initialized = True
        logger.info("LiveMarketEvidence: initialized. %d swing highs, %d swing lows, %d key levels",
                    len(self._evidence.swing_highs), len(self._evidence.swing_lows),
                    len(self._evidence.key_levels))
        
        # Seed candle history to resolve cold-start lag and populate dashboard feed
        self._seed_candles_from_history()
        self._update_indicators()

    def _seed_candles_from_history(self):
        """Populate _m15_candles and _h1_candles deques from recent MT5 bars."""
        try:
            from axonai.dataflows.mt5_data import (
                mt5_initialize, _to_mt5_symbol, _ensure_symbol_visible, _fetch_bars
            )
            if not mt5_initialize():
                logger.warning("LiveMarketEvidence: MT5 initialization failed for candle history seeding.")
                return

            mt5_sym = _to_mt5_symbol(self.symbol)
            _ensure_symbol_visible(mt5_sym)

            end_dt = datetime.now()
            # Fetch last 3 days for M15 (~288 bars) and 10 days for H1 (~240 bars) to ensure we get 100 closed bars
            df_m15 = _fetch_bars(mt5_sym, "M15", end_dt - timedelta(days=3), end_dt)
            df_h1 = _fetch_bars(mt5_sym, "H1", end_dt - timedelta(days=10), end_dt)
            df_h4 = _fetch_bars(mt5_sym, "H4", end_dt - timedelta(days=40), end_dt)

            if df_m15 is not None and not df_m15.empty:
                # Exclude the very last bar (which is the active/incomplete bar in MT5) to avoid duplicate candles
                closed_df_m15 = df_m15.iloc[:-1] if len(df_m15) > 1 else df_m15
                for open_time, row in closed_df_m15.tail(100).iterrows():
                    candle = LiveCandle(
                        timeframe="M15",
                        open_time=open_time.to_pydatetime(),
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=int(row["Volume"]),
                        is_closed=True
                    )
                    self._m15_candles.append(candle)
                logger.info("LiveMarketEvidence: seeded %d M15 historical candles", len(self._m15_candles))

            if df_h1 is not None and not df_h1.empty:
                # Exclude the very last bar (which is the active/incomplete bar in MT5) to avoid duplicate candles
                closed_df_h1 = df_h1.iloc[:-1] if len(df_h1) > 1 else df_h1
                for open_time, row in closed_df_h1.tail(100).iterrows():
                    candle = LiveCandle(
                        timeframe="H1",
                        open_time=open_time.to_pydatetime(),
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=int(row["Volume"]),
                        is_closed=True
                    )
                    self._h1_candles.append(candle)
                logger.info("LiveMarketEvidence: seeded %d H1 historical candles", len(self._h1_candles))

            if df_h4 is not None and not df_h4.empty:
                # Exclude the very last bar (which is the active/incomplete bar in MT5) to avoid duplicate candles
                closed_df_h4 = df_h4.iloc[:-1] if len(df_h4) > 1 else df_h4
                for open_time, row in closed_df_h4.tail(100).iterrows():
                    candle = LiveCandle(
                        timeframe="H4",
                        open_time=open_time.to_pydatetime(),
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=int(row["Volume"]),
                        is_closed=True
                    )
                    self._h4_candles.append(candle)
                logger.info("LiveMarketEvidence: seeded %d H4 historical candles", len(self._h4_candles))
        except Exception as e:
            logger.error("LiveMarketEvidence candle seed error: %s", e, exc_info=True)


    def on_candle_close(self, candle: LiveCandle):
        """Update structural data on candle close."""
        if not self._initialized or self._evidence is None:
            return

        if candle.timeframe == "M15":
            self._m15_candles.append(candle)
            self._detect_patterns(candle)
            self._update_indicators()
        elif candle.timeframe == "H1":
            self._h1_candles.append(candle)
            self._update_swing_points()
            self._update_key_levels(candle.close)
            self._detect_patterns(candle)
            self._update_indicators()
        elif candle.timeframe == "H4":
            self._h4_candles.append(candle)
            self._detect_patterns(candle)
            self._update_indicators()

    def _update_indicators(self):
        """Update dynamic indicators (RSI, MACD, trends) from H1 history using config-driven parameters."""
        if self._evidence is None:
            return
        candles = list(self._h1_candles)
        if len(candles) < 26:
            return

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]

        # Config parameters
        rsi_len = self.config.get("indicator_rsi_length", 14)
        ema_fast = self.config.get("indicator_ema_fast", 20)
        ema_slow = self.config.get("indicator_ema_slow", 50)

        # 1. H1 RSI
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        if len(gains) >= rsi_len:
            avg_gain = float(np.mean(gains[:rsi_len]))
            avg_loss = float(np.mean(losses[:rsi_len]))
            for i in range(rsi_len, len(gains)):
                avg_gain = (avg_gain * (rsi_len - 1) + gains[i]) / rsi_len
                avg_loss = (avg_loss * (rsi_len - 1) + losses[i]) / rsi_len
            rs = avg_gain / (avg_loss + 1e-8)
            self._evidence.rsi_h1 = float(100.0 - (100.0 / (1.0 + rs)))
        else:
            self._evidence.rsi_h1 = 50.0

        # 2. H1 MACD (12, 26, 9)
        def calc_ema(values, period):
            ema = [values[0]]
            k = 2.0 / (period + 1)
            for val in values[1:]:
                ema.append(val * k + ema[-1] * (1 - k))
            return ema

        ema12 = calc_ema(closes, 12)
        ema26 = calc_ema(closes, 26)
        macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
        signal_line = calc_ema(macd_line, 9)
        macd_diff = macd_line[-1] - signal_line[-1]

        if macd_diff > 1e-6:
            self._evidence.macd_signal_h1 = "bullish"
        elif macd_diff < -1e-6:
            self._evidence.macd_signal_h1 = "bearish"
        else:
            self._evidence.macd_signal_h1 = "neutral"

        # 3. Trend Direction H1 (using config-driven fast/slow EMAs)
        ema_f = calc_ema(closes, ema_fast)
        ema_s = calc_ema(closes, ema_slow)
        latest_close = closes[-1]
        latest_ema_f = ema_f[-1]
        latest_ema_s = ema_s[-1]

        if latest_close > latest_ema_f and latest_ema_f > latest_ema_s:
            self._evidence.trend_direction_h1 = "up"
        elif latest_close < latest_ema_f and latest_ema_f < latest_ema_s:
            self._evidence.trend_direction_h1 = "down"
        else:
            self._evidence.trend_direction_h1 = "sideways"

        # 4. Trend Direction H4 (downsample to groups of 4 H1 bars)
        h4_closes = []
        for idx in range(0, len(closes), 4):
            chunk = closes[idx:idx+4]
            if chunk:
                h4_closes.append(chunk[-1])

        if len(h4_closes) >= 10:
            ema_f_h4 = calc_ema(h4_closes, min(ema_fast, len(h4_closes)-1))
            ema_s_h4 = calc_ema(h4_closes, min(ema_slow, len(h4_closes)-1))
            l_close = h4_closes[-1]
            l_ema_f = ema_f_h4[-1]
            l_ema_s = ema_s_h4[-1]
            if l_close > l_ema_f and l_ema_f > l_ema_s:
                self._evidence.trend_direction_h4 = "up"
            elif l_close < l_ema_f and l_ema_f < l_ema_s:
                self._evidence.trend_direction_h4 = "down"
            else:
                self._evidence.trend_direction_h4 = "sideways"
        else:
            self._evidence.trend_direction_h4 = "sideways"

        # 5. Asian range / London open bias
        # Filter candles within last 24h belonging to Asian session hours (UTC 0-7)
        now_utc = datetime.now(timezone.utc)
        start_search = now_utc - timedelta(hours=24)
        m15_candles = list(self._m15_candles)
        
        broker_symbol = _to_mt5_symbol(self.symbol)
        offset_hours = get_broker_tz_offset(broker_symbol)
        
        asian_highs = []
        asian_lows = []
        for c in m15_candles:
            if c.open_time.tzinfo is None:
                c_time = c.open_time - timedelta(hours=offset_hours)
                c_time = c_time.replace(tzinfo=timezone.utc)
            else:
                c_time = c.open_time.astimezone(timezone.utc)
            if c_time >= start_search and 0 <= c_time.hour < 8:
                asian_highs.append(c.high)
                asian_lows.append(c.low)

        if asian_highs and asian_lows:
            self._evidence.asian_range_high = float(max(asian_highs))
            self._evidence.asian_range_low = float(min(asian_lows))
        else:
            # Fallback to general range of candles -12 to -4
            self._evidence.asian_range_high = float(max(highs[-12:-4])) if len(highs) >= 12 else float(max(highs))
            self._evidence.asian_range_low = float(min(lows[-12:-4])) if len(lows) >= 12 else float(min(lows))

        # London range calculation
        london_highs = []
        london_lows = []
        for c in m15_candles:
            if c.open_time.tzinfo is None:
                c_time = c.open_time - timedelta(hours=offset_hours)
                c_time = c_time.replace(tzinfo=timezone.utc)
            else:
                c_time = c.open_time.astimezone(timezone.utc)
            if c_time >= start_search and 8 <= c_time.hour < 16:
                london_highs.append(c.high)
                london_lows.append(c.low)

        if london_highs and london_lows:
            self._evidence.london_range_high = float(max(london_highs))
            self._evidence.london_range_low = float(min(london_lows))
        else:
            self._evidence.london_range_high = 0.0
            self._evidence.london_range_low = 0.0

        # London open bias
        london_close = closes[-1]
        for c in reversed(candles):
            if c.open_time.tzinfo is None:
                c_time = c.open_time - timedelta(hours=offset_hours)
                c_time = c_time.replace(tzinfo=timezone.utc)
            else:
                c_time = c.open_time.astimezone(timezone.utc)
            if c_time.hour == 8:
                london_close = c.close
                break

        if london_close > self._evidence.asian_range_high:
            self._evidence.london_open_bias = "bullish"
        elif london_close < self._evidence.asian_range_low:
            self._evidence.london_open_bias = "bearish"
        else:
            self._evidence.london_open_bias = "neutral"

        # New York range calculation (UTC 13-20)
        ny_highs = []
        ny_lows = []
        for c in m15_candles:
            if c.open_time.tzinfo is None:
                c_time = c.open_time - timedelta(hours=offset_hours)
                c_time = c_time.replace(tzinfo=timezone.utc)
            else:
                c_time = c.open_time.astimezone(timezone.utc)
            if c_time >= start_search and 13 <= c_time.hour < 21:
                ny_highs.append(c.high)
                ny_lows.append(c.low)

        if ny_highs and ny_lows:
            self._evidence.ny_range_high = float(max(ny_highs))
            self._evidence.ny_range_low = float(min(ny_lows))
        else:
            self._evidence.ny_range_high = 0.0
            self._evidence.ny_range_low = 0.0

    def _update_swing_points(self):
        """Detect new swing highs/lows from H1 candle history."""
        candles = list(self._h1_candles)
        if len(candles) < 5:
            return

        # Check the candle at index -3 (need 2 on each side)
        i = -3
        c = candles[i]
        left1, left2 = candles[i-1], candles[i-2]
        right1, right2 = candles[i+1], candles[i+2]

        # Swing high
        if (c.high > left1.high and c.high > left2.high and
                c.high > right1.high and c.high > right2.high):
            new_sh = {"price": c.high, "time": c.open_time.strftime("%Y-%m-%d %H:%M")}
            self._evidence.swing_highs.insert(0, new_sh)
            self._evidence.swing_highs = self._evidence.swing_highs[:5]  # Keep last 5

        # Swing low
        if (c.low < left1.low and c.low < left2.low and
                c.low < right1.low and c.low < right2.low):
            new_sl = {"price": c.low, "time": c.open_time.strftime("%Y-%m-%d %H:%M")}
            self._evidence.swing_lows.insert(0, new_sl)
            self._evidence.swing_lows = self._evidence.swing_lows[:5]

    def _update_key_levels(self, current_price: float):
        """Refresh key levels within 20 pips of current price."""
        pip_20 = 20 * self._pip_mult
        all_levels = set()
        for sh in self._evidence.swing_highs:
            all_levels.add(round(sh["price"], 5))
        for sl in self._evidence.swing_lows:
            all_levels.add(round(sl["price"], 5))

        self._evidence.key_levels = sorted(
            [l for l in all_levels if abs(l - current_price) <= pip_20],
            key=lambda x: abs(x - current_price)
        )[:5]

    def _detect_patterns(self, candle: LiveCandle):
        """Detect candle patterns on active timeframe."""
        patterns = []
        c_range = candle.range + 1e-8

        # Pin bar
        if candle.body / c_range < 0.30:
            if candle.upper_shadow / c_range > 0.60 and candle.close <= candle.open:
                patterns.append("pin_bar_bearish")
            elif candle.lower_shadow / c_range > 0.60 and candle.close >= candle.open:
                patterns.append("pin_bar_bullish")

        # Determine history based on timeframe
        history = []
        if candle.timeframe == "M15":
            history = list(self._m15_candles)
        elif candle.timeframe == "H1":
            history = list(self._h1_candles)
        elif candle.timeframe == "H4":
            history = list(self._h4_candles)

        # Engulfing (need previous candle)
        if len(history) >= 2:
            prev = history[-2]
            if candle.is_bullish and not prev.is_bullish:
                if candle.open <= prev.close and candle.close >= prev.open and candle.body > prev.body:
                    patterns.append("engulfing_bullish")
            elif not candle.is_bullish and prev.is_bullish:
                if candle.open >= prev.close and candle.close <= prev.open and candle.body > prev.body:
                    patterns.append("engulfing_bearish")

        if patterns:
            self._evidence.recent_patterns = patterns + self._evidence.recent_patterns[:5]

    def snapshot(self) -> MarketEvidence:
        """Return frozen copy for graph invocation."""
        if self._evidence is None:
            return extract_market_evidence(self.symbol)
        return copy.deepcopy(self._evidence)

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def key_levels(self) -> list:
        """Current key levels for event detection."""
        if self._evidence is None:
            return []
        return list(self._evidence.key_levels)

    @property
    def swing_highs(self) -> list:
        if self._evidence is None:
            return []
        return list(self._evidence.swing_highs)

    @property
    def swing_lows(self) -> list:
        if self._evidence is None:
            return []
        return list(self._evidence.swing_lows)
