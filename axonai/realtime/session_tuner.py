"""Self-configuring session selector.

Learns, per trading session, how much the *current pair* actually moves
(realized high-low range in pips), then decides which sessions are worth
trading. A session is enabled only if its average range clears two bars:

  1. movement-to-cost:   avg_range_pips >= spread_mult * avg_spread_pips
  2. relative strength:   avg_range_pips >= rel_floor  * best_session_range

Both bars use the pair's OWN spread and OWN strongest session, so the logic
is pair-agnostic (EURUSD, GBPJPY, XAUUSD ...). History is persisted to disk so
it survives restarts. Until enough samples are collected the tuner returns
None and the caller falls back to the manual ``realtime_active_sessions`` list.

The tuner never opens or closes trades — it only answers active_sessions().
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from typing import Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

_SESSIONS = ["asian", "london", "overlap", "newyork", "rollover"]


class SessionTuner:
    def __init__(self, config: Optional[dict] = None, symbol: str = "EURUSD"):
        self.config = config or {}
        self.symbol = symbol
        s = symbol.upper()
        self.pip = 0.01 if ("JPY" in s or "XAU" in s) else 0.0001

        self.window_days = int(self.config.get("realtime_auto_sessions_window", 10))
        self.min_samples = int(self.config.get("realtime_auto_sessions_min_samples", 3))
        self.spread_mult = float(self.config.get("realtime_auto_sessions_spread_mult", 25.0))
        self.rel_floor = float(self.config.get("realtime_auto_sessions_rel_floor", 0.40))

        cache_dir = self.config.get("data_cache_dir") or os.path.join(
            os.path.expanduser("~"), ".axonai", "cache"
        )
        self._path = os.path.join(cache_dir, f"session_movement_{s}.json")

        # Rolling realized-range samples (pips) per session.
        self._history: Dict[str, Deque[float]] = {
            k: deque(maxlen=self.window_days) for k in _SESSIONS
        }
        self._spread_ema: Optional[float] = None  # pips

        # Currently-accumulating session window.
        self._cur_session: Optional[str] = None
        self._cur_high: float = 0.0
        self._cur_low: float = 0.0

        self._load()

    # ── live updates ─────────────────────────────────────────────────────────
    def update_tick(self, session: Optional[str], price: float, spread_pips: float) -> None:
        """Feed one tick: update running session range + spread estimate."""
        if session not in _SESSIONS or price <= 0:
            return

        if spread_pips is not None and spread_pips >= 0:
            self._spread_ema = (
                spread_pips if self._spread_ema is None
                else 0.98 * self._spread_ema + 0.02 * spread_pips
            )

        if session != self._cur_session:
            self._finalize()  # close out the previous session window
            self._cur_session = session
            self._cur_high = price
            self._cur_low = price
        else:
            self._cur_high = max(self._cur_high, price)
            self._cur_low = min(self._cur_low, price)

    def _finalize(self) -> None:
        """Record the completed session's realized range and persist."""
        if self._cur_session is None:
            return
        rng_pips = (self._cur_high - self._cur_low) / self.pip
        if rng_pips > 0:
            self._history[self._cur_session].append(round(rng_pips, 1))
            self._save()

    # ── decision ─────────────────────────────────────────────────────────────
    def active_sessions(self) -> Optional[List[str]]:
        """Return the sessions worth trading, or None while still warming up."""
        counts = {k: len(v) for k, v in self._history.items()}
        # Warm-up done once we've observed at least min_samples days of data
        # (using the max avoids a rare session stalling activation forever).
        if not any(counts.values()) or max(counts.values()) < self.min_samples:
            return None

        avg = {k: (sum(v) / len(v)) for k, v in self._history.items() if v}
        if not avg:
            return None
        best = max(avg.values())
        spread = self._spread_ema or 0.0
        cost_floor = self.spread_mult * spread  # 0.0 if spread unknown

        active = []
        for k, a in avg.items():
            if a >= cost_floor and a >= self.rel_floor * best:
                active.append(k)
        # Preserve canonical order; guarantee at least the strongest session.
        active = [k for k in _SESSIONS if k in active]
        if not active:
            best_k = max(avg, key=avg.get)
            active = [best_k]
        return active

    def summary(self) -> str:
        avg = {k: round(sum(v) / len(v), 1) for k, v in self._history.items() if v}
        return (
            f"spread~{self._spread_ema:.2f}p " if self._spread_ema else "spread~? "
        ) + f"avg_range={avg}"

    # ── persistence ──────────────────────────────────────────────────────────
    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "history": {k: list(v) for k, v in self._history.items()},
                        "spread_ema": self._spread_ema,
                    },
                    f,
                )
        except Exception as e:
            logger.warning("SessionTuner: failed to save: %s", e)

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, vals in (data.get("history") or {}).items():
                if k in self._history:
                    self._history[k] = deque(
                        [float(x) for x in vals][-self.window_days:], maxlen=self.window_days
                    )
            self._spread_ema = data.get("spread_ema")
            logger.info("SessionTuner: loaded history %s", {k: len(v) for k, v in self._history.items()})
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("SessionTuner: failed to load: %s", e)


__all__ = ["SessionTuner"]
