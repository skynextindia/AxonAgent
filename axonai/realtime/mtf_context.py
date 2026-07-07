"""Multi-Timeframe Context Engine.

Aggregates state across M15, H1, H4, D1 to provide a unified
bias score and detect structural alignment (e.g., trend following
vs counter-trend). This replaces the hardcoded "H4 trend filter"
with a continuous strength scale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from axonai.realtime.event_types import LiveCandle


@dataclass
class MTFState:
    """Output snapshot of MTF context."""

    # ── Trend alignment ─────────────────────────────────────────
    alignment_score: float = 0.0      # -1.0 (strong bear) to +1.0 (strong bull)
    is_aligned: bool = False          # H4, H1, and M15 all agree
    
    # ── Individual timeframe biases ─────────────────────────────
    h4_bias: float = 0.0              # -1.0 to 1.0
    h1_bias: float = 0.0              # -1.0 to 1.0
    m15_bias: float = 0.0             # -1.0 to 1.0

    # ── Micro-structure contexts ────────────────────────────────
    is_pullback: bool = False         # M15 is against H4/H1 trend
    is_exhaustion_zone: bool = False  # Price extended on HTF

    # ── Key HTF Levels (Dynamic) ────────────────────────────────
    pdh: float = 0.0                  # Previous Daily High
    pdl: float = 0.0                  # Previous Daily Low
    
    # Textual description for logs/dashboard
    context_summary: str = "Neutral / Mixed"


class MTFContext:
    """Computes multi-timeframe alignment scores using EMAs and structure."""

    def __init__(self, pip_mult: float = 0.0001, config: Optional[dict] = None):
        self._pip = pip_mult
        self.config = config or {}
        
        # Latest candles per timeframe
        self._latest_candles: Dict[str, LiveCandle] = {}
        
        # EMA states per timeframe (using 20 and 50 period)
        self._emas: Dict[str, Dict[int, float]] = {
            "M15": {20: None, 50: None},
            "H1": {20: None, 50: None},
            "H4": {20: None, 50: None},
        }
        self._k20 = 2.0 / 21.0
        self._k50 = 2.0 / 51.0

        # Daily levels
        self._pdh = 0.0
        self._pdl = 0.0
        self._daily_bars: List[LiveCandle] = []

    def update_candle(self, candle: LiveCandle) -> MTFState:
        """Update state when a candle closes on any timeframe."""
        tf = candle.timeframe.upper()
        self._latest_candles[tf] = candle

        # Update EMAs for standard timeframes
        if tf in self._emas:
            c = candle.close
            if self._emas[tf][20] is None:
                self._emas[tf][20] = c
                self._emas[tf][50] = c
            else:
                self._emas[tf][20] = c * self._k20 + self._emas[tf][20] * (1 - self._k20)
                self._emas[tf][50] = c * self._k50 + self._emas[tf][50] * (1 - self._k50)

        # Update Daily levels
        if tf in ("D1", "DAILY"):
            self._daily_bars.append(candle)
            if len(self._daily_bars) > 1:
                # Use the fully closed previous day
                prev_day = self._daily_bars[-2]
                self._pdh = prev_day.high
                self._pdl = prev_day.low

        return self.get_state()

    def get_state(self) -> MTFState:
        """Calculate the current multi-timeframe alignment."""
        h4_bias = self._calculate_tf_bias("H4")
        h1_bias = self._calculate_tf_bias("H1")
        m15_bias = self._calculate_tf_bias("M15")

        # Alignment score: weighted average of timeframes
        # H4 carries most structural weight, M15 determines immediate momentum
        alignment = (h4_bias * 0.5) + (h1_bias * 0.3) + (m15_bias * 0.2)
        
        # Alignment flag: true if all 3 timeframes point the same way with conviction
        is_aligned = False
        if (h4_bias > 0.3 and h1_bias > 0.3 and m15_bias > 0.3):
            is_aligned = True
        elif (h4_bias < -0.3 and h1_bias < -0.3 and m15_bias < -0.3):
            is_aligned = True

        # Pullback detection: HTF is trending, but M15 is going against it
        is_pullback = False
        if (h4_bias > 0.5 and m15_bias < -0.2) or (h4_bias < -0.5 and m15_bias > 0.2):
            is_pullback = True

        # Extension detection (price very far from H4 EMA)
        is_extended = False
        h4_ema = self._emas["H4"][20]
        if h4_ema and "M15" in self._latest_candles:
            curr_price = self._latest_candles["M15"].close
            dist = abs(curr_price - h4_ema) / self._pip
            # 80 pips is a generic placeholder; should be dynamic based on ATR
            if dist > 80.0:
                is_extended = True

        # Context summary string
        if is_aligned:
            summary = f"Strong {'Bullish' if alignment > 0 else 'Bearish'} Alignment"
        elif is_pullback:
            summary = f"{'Bullish' if h4_bias > 0 else 'Bearish'} HTF Trend - M15 Pullback"
        else:
            summary = "Mixed / Choppy MTF Context"

        return MTFState(
            alignment_score=round(alignment, 3),
            is_aligned=is_aligned,
            h4_bias=round(h4_bias, 3),
            h1_bias=round(h1_bias, 3),
            m15_bias=round(m15_bias, 3),
            is_pullback=is_pullback,
            is_exhaustion_zone=is_extended,
            pdh=self._pdh,
            pdl=self._pdl,
            context_summary=summary
        )

    def _calculate_tf_bias(self, tf: str) -> float:
        """Calculate a -1.0 to 1.0 bias score for a specific timeframe."""
        ema20 = self._emas[tf][20]
        ema50 = self._emas[tf][50]
        
        if ema20 is None or ema50 is None:
            return 0.0
            
        diff = (ema20 - ema50) / self._pip
        
        # Normalize the difference to a [-1.0, 1.0] scale using a soft cap
        # 15 pips diff = ~1.0 score for H1/H4. 
        # (This should technically scale by timeframe ATR, but static is fine for V1)
        is_gold = self.config is not None and "XAU" in str(self.config.get("symbol", "")).upper()
        if is_gold:
            scale = 200.0 if tf == "H4" else 150.0 if tf == "H1" else 100.0
        else:
            scale = 20.0 if tf == "H4" else 15.0 if tf == "H1" else 10.0
        
        bias = max(min(diff / scale, 1.0), -1.0)
        
        # Add price location modifier
        if tf in self._latest_candles:
            c = self._latest_candles[tf].close
            # If price is crossing back over EMA20, reduce/boost bias
            if bias > 0 and c < ema20:
                bias *= 0.5  # Weakening bull trend
            elif bias < 0 and c > ema20:
                bias *= 0.5  # Weakening bear trend
                
        return bias

__all__ = ["MTFContext", "MTFState"]
