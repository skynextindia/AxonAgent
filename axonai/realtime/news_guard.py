"""Dynamic News Guard — blocks new entries around high-impact economic news.

Pair-aware: only events for the base or quote currency of the traded symbol
trigger a blackout. Impact-aware: only impact levels listed in
``news_guard_block_impacts`` (default ["High"]) count.

Calendar source: ForexFactory's free weekly JSON feed
(https://nfs.faireconomy.media/ff_calendar_thisweek.json). The feed is fetched
at most every ``news_guard_refresh_hours`` and cached to
``<data_cache_dir>/economic_calendar.json`` so the guard keeps working offline
(e.g. weekend / network blip) using the last-known calendar.

The guard never opens or closes trades — it only answers should_block_entry().
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


class NewsGuard:
    """Pair- and impact-aware economic-news blackout filter."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.enabled = bool(self.config.get("news_guard_enabled", True))
        self.block_impacts = {
            str(i).lower() for i in self.config.get("news_guard_block_impacts", ["High"])
        }
        self.pre = timedelta(minutes=int(self.config.get("news_guard_pre_minutes", 15)))
        self.post = timedelta(minutes=int(self.config.get("news_guard_post_minutes", 15)))
        self.url = self.config.get("news_guard_calendar_url", _DEFAULT_URL)
        self.refresh_interval = timedelta(
            hours=float(self.config.get("news_guard_refresh_hours", 6))
        )

        cache_dir = self.config.get("data_cache_dir") or os.path.join(
            os.path.expanduser("~"), ".axonai", "cache"
        )
        self._cache_path = os.path.join(cache_dir, "economic_calendar.json")

        # List of event dicts: {dt, currency, impact, title, forecast, previous, actual}
        self._events: List[dict] = []
        self._last_fetch: Optional[datetime] = None

    # ── calendar loading ────────────────────────────────────────────────────
    def refresh(self, now_utc: Optional[datetime] = None, force: bool = False) -> int:
        """Fetch the calendar if stale; fall back to disk cache. Returns count."""
        if not self.enabled:
            return 0
        now_utc = now_utc or datetime.now(timezone.utc)
        if (
            not force
            and self._last_fetch is not None
            and now_utc - self._last_fetch < self.refresh_interval
            and self._events
        ):
            return len(self._events)

        raw = self._fetch_remote()
        if raw is None:
            raw = self._load_cache()
        else:
            self._save_cache(raw)

        if raw is not None:
            self._events = self._parse(raw)
            self._last_fetch = now_utc
            logger.info("NewsGuard: loaded %d calendar events", len(self._events))
        else:
            logger.warning("NewsGuard: no calendar available (remote + cache failed)")
        return len(self._events)

    def _fetch_remote(self) -> Optional[list]:
        try:
            import urllib.request

            req = urllib.request.Request(self.url, headers={"User-Agent": "AxonAI/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning("NewsGuard: remote calendar fetch failed: %s", e)
            return None

    def _load_cache(self) -> Optional[list]:
        try:
            with open(self._cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _save_cache(self, raw: list) -> None:
        try:
            os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
            with open(self._cache_path, "w", encoding="utf-8") as f:
                json.dump(raw, f)
        except Exception as e:
            logger.warning("NewsGuard: failed to cache calendar: %s", e)

    @staticmethod
    def _parse(raw: list) -> List[dict]:
        """Parse ForexFactory weekly JSON rows into event dicts (keeps prev/forecast/actual)."""
        out: List[dict] = []
        for row in raw or []:
            try:
                date_str = row.get("date")
                if not date_str:
                    continue
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt = dt.astimezone(timezone.utc)
                out.append({
                    "dt": dt,
                    "currency": str(row.get("country", "")).upper(),
                    "impact": str(row.get("impact", "")),
                    "title": str(row.get("title", "")),
                    "forecast": str(row.get("forecast", "") or ""),
                    "previous": str(row.get("previous", "") or ""),
                    "actual": str(row.get("actual", "") or ""),
                })
            except Exception:
                continue
        return out

    # ── query ───────────────────────────────────────────────────────────────
    @staticmethod
    def _currencies_for(symbol: str) -> set:
        """EURUSD / EURUSDm / EURUSD=X → {"EUR", "USD"}."""
        s = "".join(c for c in (symbol or "").upper() if c.isalpha())
        return {s[:3], s[3:6]} if len(s) >= 6 else set()

    def should_block_entry(
        self, symbol: str, now_utc: Optional[datetime] = None
    ) -> Tuple[bool, str]:
        """Return (blocked, reason). Blocked if a relevant event is inside the
        [pre, post] window around ``now`` for either currency in the pair."""
        if not self.enabled or not self._events:
            return False, ""
        now_utc = now_utc or datetime.now(timezone.utc)
        ccys = self._currencies_for(symbol)
        if not ccys:
            return False, ""
        for event in self._events:
            dt = event["dt"]
            currency = event["currency"]
            impact = event["impact"]
            title = event["title"]
            if currency not in ccys:
                continue
            if impact.lower() not in self.block_impacts:
                continue
            if dt - self.pre <= now_utc <= dt + self.post:
                mins = (dt - now_utc).total_seconds() / 60.0
                when = f"in {mins:.0f}m" if mins >= 0 else f"{-mins:.0f}m ago"
                return True, f"{impact} {currency} news '{title}' {when}"
        return False, ""


__all__ = ["NewsGuard"]
