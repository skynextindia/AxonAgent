"""Candle Setup Tracker.

Detects high-probability reversal setups from M15/H1 candle closes and
maintains an active setup window (15 minutes) for the EntryStateMachine to
find the optimistic tick-level entry.

Detects:
1. Two-candle sweep confirmation  (old system's primary signal)
2. Pin Bar or Engulfing at S/R    (old system's pattern gate)
3. H4 trend alignment             (old system's trend gate)

Outputs a setup_score (0.0–1.0) and setup_direction ("BUY"/"SELL") that
are fed into the Unified Confluence Score in reversal_model.py.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

from axonai.realtime.event_types import LiveCandle

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candle_body_ratio(c: LiveCandle) -> float:
    r = c.range + 1e-8
    return c.body / r


def _is_pin_bar(c: LiveCandle) -> Optional[str]:
    """Return 'BUY' (bullish pin / hammer) or 'SELL' (bearish pin / shooting star), else None."""
    if _candle_body_ratio(c) >= 0.30:
        return None
    r = c.range + 1e-8
    if c.lower_shadow / r > 0.60 and c.close >= c.open:
        return "BUY"
    if c.upper_shadow / r > 0.60 and c.close <= c.open:
        return "SELL"
    return None


def _is_engulfing(curr: LiveCandle, prev: LiveCandle) -> Optional[str]:
    """Return 'BUY' (bullish engulfing) or 'SELL' (bearish engulfing), else None."""
    if curr.is_bullish and not prev.is_bullish:
        if curr.open <= prev.close and curr.close >= prev.open and curr.body > prev.body:
            return "BUY"
    elif not curr.is_bullish and prev.is_bullish:
        if curr.open >= prev.close and curr.close <= prev.open and curr.body > prev.body:
            return "SELL"
    return None


# ---------------------------------------------------------------------------
# Pending sweep state
# ---------------------------------------------------------------------------

@dataclass
class _PendingSweep:
    swept_level: float
    direction: str          # "BUY" or "SELL"
    pierce_pips: float
    candles_since: int = 0  # confirmed on candle_since == 1


# ---------------------------------------------------------------------------
# Candle Setup Tracker
# ---------------------------------------------------------------------------

@dataclass
class CandleSetup:
    """Active confirmed setup ready for tick-level entry."""
    direction: str                  # "BUY" or "SELL"
    score: float                    # 0.0 – 1.0
    reason: str                     # Human-readable description
    expiry: datetime                # When this setup expires
    source: str = ""                # "sweep", "pin_bar", "engulfing", "combined"
    confirmed_level: float = 0.0    # nearest S/R level that triggered this


class CandleSetupTracker:
    """Detects high-probability reversal setups on M15 candle closes.

    The tracker is stateless across symbols — each daemon instance per symbol
    creates its own tracker (same pattern as VelocityTrailingManager).

    Usage (inside ReversalModel):
        self.candle_setup = CandleSetupTracker(config)
        # on candle close:
        self.candle_setup.on_candle_close(candle, price_levels, h4_bias, h1_atr, pip)
        # on tick:
        if self.candle_setup.setup_active:
            direction = self.candle_setup.setup_direction
    """

    def __init__(self, config: Optional[dict] = None, pip: float = 0.0001):
        self._config = config or {}
        self._pip = pip
        self._expiry_minutes: int = int(self._config.get("candle_setup_expiry_minutes", 15))
        self._sr_proximity_pips: float = float(self._config.get("candle_setup_sr_proximity_pips", 5.0))

        # Pending two-candle sweep queue
        self._pending_sweeps: List[_PendingSweep] = []

        # Per-timeframe candle history for engulfing detection (last 3 candles)
        self._m15_history: deque[LiveCandle] = deque(maxlen=6)
        self._h1_history: deque[LiveCandle] = deque(maxlen=6)

        # Active confirmed setup (None = no setup)
        self._active: Optional[CandleSetup] = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def setup_active(self) -> bool:
        return self._active is not None

    @property
    def setup_direction(self) -> Optional[str]:
        return self._active.direction if self._active else None

    @property
    def setup_score(self) -> float:
        return self._active.score if self._active else 0.0

    @property
    def active_setup(self) -> Optional[CandleSetup]:
        return self._active

    def expire_if_stale(self, now: Optional[datetime] = None) -> None:
        """Discard the active setup if its expiry has passed."""
        if self._active is None:
            return
        now = now or datetime.utcnow()
        if now >= self._active.expiry:
            logger.info(
                "CandleSetupTracker: Setup %s expired at %s",
                self._active.direction, self._active.expiry
            )
            self._active = None

    def clear(self) -> None:
        """Manually clear the active setup (e.g. after a trade is entered)."""
        self._active = None
        self._pending_sweeps.clear()

    # ------------------------------------------------------------------
    # Core update
    # ------------------------------------------------------------------

    def on_candle_close(
        self,
        candle: LiveCandle,
        price_levels: list,
        h4_bias: float,          # MTFState.h4_bias  (−1 to +1)
        h1_atr: float,
        pip: Optional[float] = None,
    ) -> None:
        """Evaluate a closed candle for high-probability reversal setups.

        Only processes M15 and H1 candles (same as old event_detector).
        """
        if pip is not None:
            self._pip = pip

        tf = candle.timeframe.upper()
        if tf not in ("M15", "H1"):
            return

        # ── update history ──────────────────────────────────────────
        if tf == "M15":
            self._m15_history.append(candle)
        elif tf == "H1":
            self._h1_history.append(candle)

        history = list(self._m15_history if tf == "M15" else self._h1_history)

        # ── Step 1: check pending sweeps for confirmation ───────────
        self._check_sweep_confirmation(candle, price_levels, h4_bias, h1_atr)

        # ── Step 2: detect new sweep phase 1 ────────────────────────
        if tf == "M15":
            self._detect_new_sweep(candle, price_levels, h1_atr)

        # ── Step 3: candlestick patterns at S/R ─────────────────────
        self._check_pattern_at_level(candle, history, price_levels, h4_bias, h1_atr)

    # ------------------------------------------------------------------
    # Two-candle sweep logic
    # ------------------------------------------------------------------

    def _detect_new_sweep(
        self,
        candle: LiveCandle,
        price_levels: list,
        h1_atr: float,
    ) -> None:
        """Phase 1: candle pierces a level but closes back inside → store as pending."""
        pip = self._pip
        prox = self._sr_proximity_pips * pip

        for lvl in price_levels:
            if not getattr(lvl, "is_active", False):
                continue
            lp = lvl.price
            lvl_dir = getattr(lvl, "direction", "")

            # Support sweep: wick low below level, close above level
            if candle.low < lp and candle.close > lp and lvl_dir in ("support", "current", ""):
                pierce_pips = (lp - candle.low) / pip
                if pierce_pips < 0.5:  # noise filter: at least 0.5 pips pierce
                    continue
                # Avoid duplicates
                if not any(abs(p.swept_level - lp) < prox for p in self._pending_sweeps):
                    self._pending_sweeps.append(
                        _PendingSweep(swept_level=lp, direction="BUY",
                                      pierce_pips=round(pierce_pips, 1))
                    )
                    logger.info(
                        "CandleSetupTracker: Pending BUY sweep at %.5f (pierce=%.1f pips)",
                        lp, pierce_pips
                    )

            # Resistance sweep: wick high above level, close below level
            elif candle.high > lp and candle.close < lp and lvl_dir in ("resistance", "current", ""):
                pierce_pips = (candle.high - lp) / pip
                if pierce_pips < 0.5:
                    continue
                if not any(abs(p.swept_level - lp) < prox for p in self._pending_sweeps):
                    self._pending_sweeps.append(
                        _PendingSweep(swept_level=lp, direction="SELL",
                                      pierce_pips=round(pierce_pips, 1))
                    )
                    logger.info(
                        "CandleSetupTracker: Pending SELL sweep at %.5f (pierce=%.1f pips)",
                        lp, pierce_pips
                    )

    def _check_sweep_confirmation(
        self,
        candle: LiveCandle,
        price_levels: list,
        h4_bias: float,
        h1_atr: float,
    ) -> None:
        """Phase 2: if next candle closes in the reversal direction → confirm sweep."""
        expired = []
        for ps in self._pending_sweeps:
            ps.candles_since += 1

            # Expire after 3 candles without confirmation (old system logic)
            if ps.candles_since > 3:
                expired.append(ps)
                continue

            # Only check on the first confirmation candle
            if ps.candles_since != 1:
                continue

            confirmed = False
            if ps.direction == "BUY" and candle.is_bullish:
                confirmed = True
            elif ps.direction == "SELL" and not candle.is_bullish:
                confirmed = True

            if confirmed:
                expired.append(ps)  # consume this pending sweep
                score = self._score_sweep(ps, candle, h4_bias, h1_atr)
                self._emit_setup(
                    direction=ps.direction,
                    score=score,
                    reason=f"Two-candle sweep confirmed at {ps.swept_level:.5f} ({ps.pierce_pips:.1f} pips pierce)",
                    source="sweep",
                    level=ps.swept_level,
                )

        for ps in expired:
            if ps in self._pending_sweeps:
                self._pending_sweeps.remove(ps)

    def _score_sweep(
        self, ps: _PendingSweep, confirm_candle: LiveCandle,
        h4_bias: float, h1_atr: float,
    ) -> float:
        """Score a confirmed sweep 0.5–1.0 based on pierce depth and trend alignment."""
        score = 0.60  # base for a confirmed two-candle sweep
        pip = self._pip

        # Deeper pierce = more institutional activity = higher score
        atr_pips = h1_atr / pip if h1_atr > 0 else 30.0
        pierce_ratio = ps.pierce_pips / atr_pips
        score += min(0.15, pierce_ratio * 0.3)

        # Trend alignment bonus (H4 bias in the same direction as the reversal)
        want = 1 if ps.direction == "BUY" else -1
        if h4_bias * want > 0.3:
            score += 0.15   # with H4 trend
        elif h4_bias * want < -0.3:
            score -= 0.10   # against H4 trend

        # Strong confirming candle (large body)
        body_ratio = _candle_body_ratio(confirm_candle)
        if body_ratio > 0.6:
            score += 0.10

        return min(1.0, max(0.40, score))

    # ------------------------------------------------------------------
    # Candlestick pattern at S/R level
    # ------------------------------------------------------------------

    def _check_pattern_at_level(
        self,
        candle: LiveCandle,
        history: list,
        price_levels: list,
        h4_bias: float,
        h1_atr: float,
    ) -> None:
        """Detect Pin Bar or Engulfing candle within S/R proximity."""
        pip = self._pip
        prox_pips = self._sr_proximity_pips
        prox = prox_pips * pip

        # Detect pattern type
        pattern_dir = _is_pin_bar(candle)
        pattern_src = "pin_bar"
        if pattern_dir is None and len(history) >= 2:
            pattern_dir = _is_engulfing(candle, history[-2])
            pattern_src = "engulfing"
        if pattern_dir is None:
            return

        # Check if close is within proximity of any active level
        nearest_level: Optional[float] = None
        nearest_dist = float("inf")
        for lvl in price_levels:
            if not getattr(lvl, "is_active", False):
                continue
            dist = abs(candle.close - lvl.price)
            if dist < prox and dist < nearest_dist:
                nearest_dist = dist
                nearest_level = lvl.price

        if nearest_level is None:
            return

        dist_pips = nearest_dist / pip
        score = self._score_pattern(pattern_dir, dist_pips, prox_pips, h4_bias)
        self._emit_setup(
            direction=pattern_dir,
            score=score,
            reason=f"{pattern_src.replace('_', ' ').title()} at S/R {nearest_level:.5f} ({dist_pips:.1f} pips away)",
            source=pattern_src,
            level=nearest_level,
        )

    def _score_pattern(
        self, direction: str, dist_pips: float, prox_pips: float,
        h4_bias: float,
    ) -> float:
        """Score a candlestick pattern 0.45–0.90 based on proximity and trend."""
        # Base: closer to level = stronger
        proximity_ratio = 1.0 - (dist_pips / prox_pips)
        score = 0.45 + 0.25 * proximity_ratio  # 0.45 – 0.70

        # H4 trend alignment
        want = 1 if direction == "BUY" else -1
        if h4_bias * want > 0.3:
            score += 0.15
        elif h4_bias * want < -0.3:
            score -= 0.10

        return min(0.90, max(0.35, score))

    # ------------------------------------------------------------------
    # Setup emission (de-dup: only upgrade score, never downgrade)
    # ------------------------------------------------------------------

    def _emit_setup(
        self,
        direction: str,
        score: float,
        reason: str,
        source: str,
        level: float,
    ) -> None:
        now = datetime.utcnow()
        expiry = now + timedelta(minutes=self._expiry_minutes)

        if self._active is not None:
            # Same direction: upgrade score if better
            if self._active.direction == direction and score > self._active.score:
                self._active.score = score
                self._active.reason = reason
                self._active.source = source
                self._active.expiry = expiry  # refresh expiry
                logger.info(
                    "CandleSetupTracker: Setup upgraded → %s score=%.2f (%s)",
                    direction, score, reason
                )
            # Opposite direction: only replace if significantly better
            elif self._active.direction != direction and score > self._active.score + 0.15:
                logger.info(
                    "CandleSetupTracker: Setup FLIPPED %s→%s score=%.2f (%s)",
                    self._active.direction, direction, score, reason
                )
                self._active = CandleSetup(
                    direction=direction, score=score, reason=reason,
                    expiry=expiry, source=source, confirmed_level=level
                )
            return

        # No active setup → emit fresh
        self._active = CandleSetup(
            direction=direction, score=score, reason=reason,
            expiry=expiry, source=source, confirmed_level=level
        )
        logger.info(
            "CandleSetupTracker: New setup %s score=%.2f expiry=%s | %s",
            direction, score, expiry.strftime("%H:%M:%S"), reason
        )


__all__ = ["CandleSetupTracker", "CandleSetup"]
