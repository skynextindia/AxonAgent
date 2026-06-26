"""Shared entry-quality gate for microstructure-peak reversals.

Single source of truth used by BOTH the backtester and the live daemon so the
dry-run / live path trades the exact same quality-filtered strategy the
backtest was tuned on. Mirrors backtester._evaluate_event's PEAK_DETECTION
path (intensity/confidence gate, S/R proximity, H4 trend alignment, confluence
scoring, level-behaviour, regime, MTF boost, quality floor, duplicate-side).

Stateful gates that depend on trade history (max-concurrent, session window,
entry/loss cooldowns) stay in each caller, which already manages that state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Iterable

EXHAUSTION_PEAKS = ("velocity_exhaustion", "microstructure_exhaustion")
SR_PROXIMITY_PIPS = 10.0  # Relaxed from 5.0 - allow more entries away from exact levels
DEFAULT_MIN_QUALITY = 0.50  # Relaxed from 0.55


@dataclass
class EntryDecision:
    passed: bool
    direction: Optional[str] = None      # "BUY" | "SELL" | None
    signal_quality: float = 0.0
    reason: str = ""                      # why it passed
    skip_reason: str = ""                 # why it was rejected


def _direction_from(dir_str: str) -> Optional[str]:
    """Map an event direction string to a trade side (fail closed)."""
    d = (dir_str or "").lower()
    if "bullish" in d:
        return "BUY"
    if "bearish" in d:
        return "SELL"
    return None


def evaluate_peak_entry(
    event,
    *,
    price_levels: Iterable,
    pip_mult: float,
    trend_h4: str = "sideways",
    dominant_regime: str = "",
    active_directions: Optional[Iterable[str]] = None,
    config: Optional[dict] = None,
) -> EntryDecision:
    """Decide whether a PEAK_DETECTION event qualifies for entry.

    Returns an EntryDecision. ``passed`` is True only when every quality gate is
    satisfied; otherwise ``skip_reason`` explains the rejection.
    """
    config = config or {}
    active_directions = set(active_directions or ())
    details = getattr(event, "details", {}) or {}

    peak_type = details.get("peak_type", "")
    if peak_type not in EXHAUSTION_PEAKS:
        return EntryDecision(False, skip_reason=f"not an exhaustion peak ({peak_type or 'n/a'})")

    confidence = details.get("peak_confidence", 0.0)
    confirmed = details.get("peak_confirmed", False)
    intensity = details.get("intensity", "MEDIUM")

    # Intensity / confidence gate (RELAXED for more entries)
    if intensity not in ("HIGH", "MEDIUM", "LOW"):  # Allow LOW intensity too
        return EntryDecision(False, skip_reason=f"intensity {intensity}")
    # Relaxed confidence requirement for unconfirmed peaks
    if intensity == "HIGH" and not confirmed and confidence < 0.40:  # was 0.6
        return EntryDecision(False, skip_reason=f"HIGH peak unconfirmed (conf={confidence:.2f})")

    # Direction (fail closed)
    direction = _direction_from(details.get("direction", ""))
    if direction is None:
        return EntryDecision(False, skip_reason="indeterminate entry direction")
    reason = f"{direction} Microstructure Peak ({peak_type})"

    # S/R proximity — must be within 5 pips of an active level (backtester :612-627)
    closest_dist = float("inf")
    closest_lvl = None
    for lvl in price_levels:
        if not getattr(lvl, "is_active", True):
            continue
        dist = abs(event.price - lvl.price) / pip_mult
        if dist < closest_dist:
            closest_dist = dist
            closest_lvl = lvl
    if closest_lvl is None or closest_dist > SR_PROXIMITY_PIPS:
        return EntryDecision(False, direction=direction,
                             skip_reason=f"not near S/R zone (closest {closest_dist:.2f} pips)")

    # H4 daily-trend alignment (backtester :629-636)
    if trend_h4 == "up" and direction != "BUY":
        return EntryDecision(False, direction=direction, skip_reason="counter H4 uptrend")
    if trend_h4 == "down" and direction != "SELL":
        return EntryDecision(False, direction=direction, skip_reason="counter H4 downtrend")

    # Confluence quality score (backtester :641-653)
    if intensity == "MEDIUM":
        sc = details.get("swing_confidence", None)
        signal_quality = (0.5 + 0.3 * sc) if sc is not None else 0.65
    else:
        signal_quality = 0.3 + confidence * 0.5
    if confirmed:
        signal_quality += 0.2

    # Level-behaviour gate (backtester :701-704)
    level_behavior = details.get("level_behavior", {}) or {}
    if level_behavior.get("attack_quality", "") in ("weakening", "pressured"):
        return EntryDecision(False, direction=direction, signal_quality=signal_quality,
                             skip_reason="level weakening/pressured")

    # Regime filter (backtester :706-710)
    if dominant_regime == "compression":
        return EntryDecision(False, direction=direction, signal_quality=signal_quality,
                             skip_reason="compression regime")

    # MTF alignment boost (backtester :712-716)
    mtf = details.get("mtf_alignment", "NEUTRAL")
    if (direction == "BUY" and mtf == "BULLISH") or (direction == "SELL" and mtf == "BEARISH"):
        signal_quality = min(1.0, signal_quality + 0.15)

    # Quality floor (backtester :718-721)
    min_quality = config.get("realtime_min_signal_quality", DEFAULT_MIN_QUALITY)
    if signal_quality < min_quality:
        return EntryDecision(False, direction=direction, signal_quality=signal_quality,
                             skip_reason=f"quality {signal_quality:.2f} < floor {min_quality:.2f}")

    # Duplicate-direction gate (backtester :723-726)
    if direction in active_directions:
        return EntryDecision(False, direction=direction, signal_quality=signal_quality,
                             skip_reason=f"{direction} already open")

    return EntryDecision(True, direction=direction, signal_quality=signal_quality, reason=reason)
