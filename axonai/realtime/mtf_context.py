"""Multi-Timeframe Context Engine.

Aggregates state across M15, H1, H4, D1 to provide a unified
bias score and detect structural alignment (e.g., trend following
vs counter-trend). This replaces the hardcoded "H4 trend filter"
with a continuous strength scale.
"""

from __future__ import annotations

from collections import deque
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
    is_exhaustion_zone: bool = False  # Price extended on HTF (ATR-normalized)

    # ── Reversal / inception signals (NOT trend-derived) ────────
    # reversal_pressure: 0..1 blend of velocity decay + displacement exhaustion,
    # populated by ReversalModel.on_tick (MTFContext has no tick data). Lets the
    # entry gate allow a counter-trend reversal that the EMA bias would veto.
    reversal_pressure: float = 0.0
    structure_break: bool = False     # M15 swing-structure flip (BOS/CHoCH)
    structure_break_dir: int = 0      # +1 bullish break, -1 bearish break, 0 none

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

        # H4 ATR tracking (for ATR-normalized exhaustion, replaces 80-pip literal)
        self._h4_tr = deque(maxlen=14)
        self._h4_prev_close: Optional[float] = None
        self._h4_atr: float = 0.0

        # M15 swing structure window (for BOS/CHoCH structure-break detection)
        self._m15_highs = deque(maxlen=6)
        self._m15_lows = deque(maxlen=6)
        self._structure_dir: int = 0  # last confirmed structure direction

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

        # Track H4 ATR (14-period) for ATR-normalized exhaustion
        if tf == "H4":
            tr = candle.high - candle.low
            if self._h4_prev_close is not None:
                tr = max(tr, abs(candle.high - self._h4_prev_close), abs(candle.low - self._h4_prev_close))
            self._h4_prev_close = candle.close
            self._h4_tr.append(tr)
            if len(self._h4_tr) >= 3:
                self._h4_atr = sum(self._h4_tr) / len(self._h4_tr)

        # Track M15 swing highs/lows for structure-break detection
        if tf == "M15":
            self._m15_highs.append(candle.high)
            self._m15_lows.append(candle.low)

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

        # Extension detection (price far from H4 EMA), ATR-normalized per pair.
        # Threshold = mult * H4_ATR; falls back to the legacy 80-pip literal only
        # until the H4 ATR warms. This is what makes exhaustion detection scale to
        # XAUUSD instead of using an FX-tuned constant.
        is_extended = False
        h4_ema = self._emas["H4"][20]
        if h4_ema and "M15" in self._latest_candles:
            curr_price = self._latest_candles["M15"].close
            dist_pips = abs(curr_price - h4_ema) / self._pip
            atr_pips = (self._h4_atr / self._pip) if self._h4_atr > 0 else 0.0
            mult = float(self.config.get("mtf_exhaustion_atr_mult", 1.5))  # was 2.0 — fired <4%; lowered so exhaustion actually registers
            thresh = (mult * atr_pips) if atr_pips > 0 else 80.0
            if dist_pips > thresh:
                is_extended = True

        # Structure-break (BOS/CHoCH): a new lower-low after a rising sequence
        # (bearish CHoCH) or a new higher-high after a falling sequence (bullish).
        structure_break = False
        structure_break_dir = 0
        if len(self._m15_highs) >= 4:
            highs = list(self._m15_highs)
            lows = list(self._m15_lows)
            prior_hi = max(highs[:-1])
            prior_lo = min(lows[:-1])
            if lows[-1] < prior_lo and self._structure_dir >= 0:
                structure_break = True
                structure_break_dir = -1
                self._structure_dir = -1
            elif highs[-1] > prior_hi and self._structure_dir <= 0:
                structure_break = True
                structure_break_dir = 1
                self._structure_dir = 1

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
            structure_break=structure_break,
            structure_break_dir=structure_break_dir,
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
