"""Core MTF structure computation. Pure Python, no deps.

For each timeframe window (given in BARS of the base series), classify:
  * trend   — UP / DOWN / RANGE, via the Kaufman efficiency ratio + net sign.
              ER = |net move| / total path; high ER + a directional net = a trend,
              low ER = choppy range. This is the multi-day/multi-scale trend that
              actually holds (the intraday one is a random walk).
  * position — where the CURRENT price sits inside that window's high-low range
              (0 % = at the low, 100 % = at the high).

The point is CONTEXT, not prediction: "short-term UP into a multi-year range top"
is a structural fact the machine can act on, even though the next tick is a coin flip.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any

ER_TREND_THRESHOLD = 0.30   # ER >= this + directional net => trending, else range


@dataclass
class TFStructure:
    name: str
    trend: str                 # "UP" | "DOWN" | "RANGE"
    net_pips: float
    efficiency_ratio: float
    range_hi: float
    range_lo: float
    position_pct: float        # 0..100, where current price sits in the range
    bars: int

    @property
    def position_label(self) -> str:
        p = self.position_pct
        if p >= 80: return "near top"
        if p >= 60: return "upper"
        if p > 40:  return "mid"
        if p > 20:  return "lower"
        return "near bottom"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["position_label"] = self.position_label
        return d


def classify_tf(name: str, highs: List[float], lows: List[float],
                closes: List[float], cur: float, pip: float,
                bars: int, er_threshold: float = ER_TREND_THRESHOLD
                ) -> Optional[TFStructure]:
    """Classify one timeframe window (the last `bars` bars). None if too short."""
    if bars < 2 or len(closes) < bars:
        return None
    c = closes[-bars:]; h = highs[-bars:]; l = lows[-bars:]
    net = (c[-1] - c[0]) / pip
    hi = max(h); lo = min(l)
    path = sum(abs(c[k] - c[k - 1]) for k in range(1, len(c)))
    er = abs(c[-1] - c[0]) / path if path > 0 else 0.0
    if er >= er_threshold and net > 0:
        trend = "UP"
    elif er >= er_threshold and net < 0:
        trend = "DOWN"
    else:
        trend = "RANGE"
    pos = (cur - lo) / (hi - lo) * 100 if hi > lo else 50.0
    return TFStructure(name=name, trend=trend, net_pips=round(net, 1),
                       efficiency_ratio=round(er, 3), range_hi=round(hi, 5),
                       range_lo=round(lo, 5), position_pct=round(pos, 1), bars=bars)


@dataclass
class MTFSnapshot:
    price: float
    tfs: List[TFStructure] = field(default_factory=list)

    def get(self, name: str) -> Optional[TFStructure]:
        return next((t for t in self.tfs if t.name == name), None)

    def summary(self) -> str:
        return "  ".join(f"{t.name}={t.trend}" for t in self.tfs)

    def fade_read(self) -> Dict[str, Any]:
        """A STRUCTURAL assessment for a fade decision — heuristic, NOT validated.
        Flags the two things that matter for fading a top/bottom:
          higher_tf_extreme : is price at a range extreme on the SLOW timeframes
                              (the ones that classify as RANGE, where extremes mean-revert)?
          short_tf_momentum : the trend on the FAST timeframes (against the fade = risk).
        """
        slow = [t for t in self.tfs if t.bars >= 63]      # 3mo and up
        fast = [t for t in self.tfs if t.bars <= 21]      # 1mo and down
        # extreme on the slow (range) frames
        slow_range = [t for t in slow if t.trend == "RANGE"]
        top_extreme = any(t.position_pct >= 80 for t in slow_range)
        bot_extreme = any(t.position_pct <= 20 for t in slow_range)
        # momentum on the fast frames
        fast_up = sum(1 for t in fast if t.trend == "UP")
        fast_down = sum(1 for t in fast if t.trend == "DOWN")
        mom = "UP" if fast_up > fast_down else "DOWN" if fast_down > fast_up else "FLAT"
        # the classic tension: at a slow-frame top but fast momentum still UP
        note = "neutral"
        if top_extreme and mom == "UP":
            note = "SELL-fade risky: at a higher-TF range TOP but short-term momentum still UP (breakout risk)"
        elif top_extreme and mom != "UP":
            note = "SELL-fade supported: higher-TF range TOP and short-term momentum not up"
        elif bot_extreme and mom == "DOWN":
            note = "BUY-fade risky: at a higher-TF range BOTTOM but short-term momentum still DOWN"
        elif bot_extreme and mom != "DOWN":
            note = "BUY-fade supported: higher-TF range BOTTOM and short-term momentum not down"
        return {"higher_tf_extreme_top": top_extreme, "higher_tf_extreme_bottom": bot_extreme,
                "short_tf_momentum": mom, "note": note}

    def premium_discount(self) -> Dict[str, Any]:
        """PREMIUM / DISCOUNT read across the nested stack (SMC/supply-demand style).
        A range's upper half is a PREMIUM (expensive -> sell/supply bias); the lower
        half is a DISCOUNT (cheap -> buy/demand bias); the middle is equilibrium.
        The BIGGER timeframes set the directional bias; the intraday tells you where
        price currently is inside that bias (the entry timing). Heuristic, NOT yet
        P&L-validated (see note)."""
        def zone(p):
            if p >= 75: return "deep-premium"
            if p >= 60: return "premium"
            if p > 40:  return "equilibrium"
            if p > 25:  return "discount"
            return "deep-discount"
        per = {t.name: {"pos": t.position_pct, "zone": zone(t.position_pct)} for t in self.tfs}
        macro = [t.position_pct for t in self.tfs if t.name in ("5Y", "1Y", "3M")]
        intr = [t.position_pct for t in self.tfs if t.name in ("1D", "1H", "15M", "5M")]
        macro_pos = sum(macro) / len(macro) if macro else 50.0
        intr_pos = sum(intr) / len(intr) if intr else 50.0
        macro_zone = zone(macro_pos); intr_zone = zone(intr_pos)
        # Bias comes from the bigger TFs; entry timing from intraday.
        if macro_pos >= 60:
            bias = "SELL / supply (macro PREMIUM — price expensive vs bigger TFs)"
            timing = ("price already pulled to intraday DISCOUNT — wait for a bounce "
                      "back toward intraday premium to sell" if intr_pos < 45 else
                      "price at intraday premium too — a sell can be timed here")
        elif macro_pos <= 40:
            bias = "BUY / demand (macro DISCOUNT — price cheap vs bigger TFs)"
            timing = ("price at intraday PREMIUM — wait for a dip to intraday discount "
                      "to buy" if intr_pos > 55 else
                      "price at intraday discount too — a buy can be timed here")
        else:
            bias = "NEUTRAL (macro equilibrium — no discount/premium edge)"
            timing = "no clear location edge; stand aside or scalp both ways"
        return {"macro_pos": round(macro_pos, 1), "macro_zone": macro_zone,
                "intraday_pos": round(intr_pos, 1), "intraday_zone": intr_zone,
                "bias": bias, "timing": timing, "per_tf": per}

    def to_dict(self) -> Dict[str, Any]:
        return {"price": self.price, "summary": self.summary(),
                "fade_read": self.fade_read(),
                "premium_discount": self.premium_discount(),
                "timeframes": [t.to_dict() for t in self.tfs]}


# Standard windows in TRADING DAYS (base series = daily bars)
DAILY_WINDOWS: Dict[str, int] = {
    "5Y": 1260, "1Y": 252, "3M": 63, "1M": 21, "1W": 5,
}


def compute_mtf(highs: List[float], lows: List[float], closes: List[float],
                pip: float, windows: Optional[Dict[str, int]] = None) -> MTFSnapshot:
    """Build the full top-down snapshot from a base series (daily bars)."""
    windows = windows or DAILY_WINDOWS
    cur = closes[-1]
    tfs: List[TFStructure] = []
    # largest window first (top-down)
    for name, bars in sorted(windows.items(), key=lambda kv: -kv[1]):
        tf = classify_tf(name, highs, lows, closes, cur, pip, bars)
        if tf is not None:
            tfs.append(tf)
    return MTFSnapshot(price=round(cur, 5), tfs=tfs)
