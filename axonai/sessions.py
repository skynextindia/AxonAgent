"""Single source of truth for forex trading-session math.

Every session boundary, DST rule, and session label in AxonAI is derived here so
there is exactly ONE daylight-saving computation in the codebase. It uses real
IANA timezone data (``zoneinfo``) rather than hand-rolled "nth Sunday of the
month" arithmetic — correct across DST transitions and, crucially, *identical*
for every caller. Historically six modules each rolled their own version and
they disagreed (e.g. the New York close was variously 14:00, 16:00, and read off
the local machine clock), which is exactly the drift this module removes.

Three views, all zoneinfo-based, all keyed off the *date* of the passed instant
so DST is automatic:

- ``get_dst_session_hours(dt)`` — analytic boundaries as UTC hours-of-day:
  ``(ldn_open, ldn_close, ny_open, ny_close)``. London is 08:00–16:00
  Europe/London. ``ny_close`` is the **14:00 New-York liquidity/rollover
  anchor** (the EOD flatten keys off ``ny_close + 3h`` = the 5 pm NY daily
  rollover) — it is deliberately NOT the 5 pm session close. This is the
  boundary set the trade levels, the session classifier, the flatten schedule,
  and the range extractors all share.

- ``classify_session(dt)`` — ``(session, hours_since_london_open)`` where session
  is ``overlap | london | newyork | rollover | asian``. Callers own the
  ``session_penalty`` policy (it differs: config-gated Asian suppression in the
  live daemon vs a fixed weight in the batch scorer).

- ``session_hud(dt)`` — human dashboard bars for Sydney/Tokyo/London/New York
  with *display* closes (NY 16:00 local), each active-state + progress computed.
  Display only; never feed these back into trade logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Tuple
from zoneinfo import ZoneInfo

_LDN = ZoneInfo("Europe/London")
_NY = ZoneInfo("America/New_York")
_SYD = ZoneInfo("Australia/Sydney")


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utc_hour_of(local_dt_date, tz: ZoneInfo, hh: int, mm: int = 0) -> float:
    """UTC hour-of-day for ``hh:mm`` wall-clock in ``tz`` on the given local date."""
    local = datetime(local_dt_date.year, local_dt_date.month, local_dt_date.day, hh, mm, tzinfo=tz)
    u = local.astimezone(timezone.utc)
    return u.hour + u.minute / 60.0


def get_dst_session_hours(dt: datetime) -> Tuple[float, float, float, float]:
    """Return ``(ldn_open, ldn_close, ny_open, ny_close)`` as UTC hours-of-day.

    DST-correct via zoneinfo. ``ny_close`` is the 14:00-New-York rollover anchor
    (see module docstring), not the 5 pm session close. Values are whole hours
    for these markets, but returned as floats for uniform arithmetic.
    """
    u = _as_utc(dt)
    dl = u.astimezone(_LDN)
    dn = u.astimezone(_NY)
    return (
        _utc_hour_of(dl, _LDN, 8),    # London open  08:00 local
        _utc_hour_of(dl, _LDN, 16),   # London close 16:00 local
        _utc_hour_of(dn, _NY, 8),     # NY open      08:00 local
        _utc_hour_of(dn, _NY, 14),    # NY rollover anchor 14:00 local
    )


def classify_session(dt: datetime) -> Tuple[str, float]:
    """Classify ``dt`` into a trading session + hours-since-London-open.

    Buckets (by UTC hour ``h``, using the analytic boundaries above):
      overlap  : ny_open   <= h < ldn_close
      london   : ldn_open  <= h < ny_open
      newyork  : ldn_close <= h < ny_close
      rollover : ny_close  <= h < ny_close + 1
      asian    : otherwise
    ``hours_since_london_open`` wraps past midnight so it is always >= 0.
    """
    u = _as_utc(dt)
    h = u.hour + u.minute / 60.0
    ldn_open, ldn_close, ny_open, ny_close = get_dst_session_hours(u)
    hrs = (h - ldn_open) if h >= ldn_open else (h + 24.0 - ldn_open)

    if ny_open <= h < ldn_close:
        return "overlap", hrs
    if ldn_open <= h < ny_open:
        return "london", hrs
    if ldn_close <= h < ny_close:
        return "newyork", hrs
    if ny_close <= h < (ny_close + 1.0):
        return "rollover", hrs
    return "asian", hrs


def session_hud(dt: datetime) -> List[dict]:
    """Human dashboard session bars (Sydney/Tokyo/London/New York), each with
    active-state, progress and remaining minutes. Display only.

    Windows (local wall-clock → UTC via zoneinfo): Sydney 08:00–17:00
    Australia/Sydney; Tokyo 09:00–18:00 Asia/Tokyo (= 00:00–09:00 UTC, no DST);
    London 08:00–16:00 Europe/London; New York 08:00–16:00 America/New_York
    (16:00 = the human 4 pm close, distinct from the 14:00 analytic anchor).
    """
    u = _as_utc(dt)
    dl = u.astimezone(_LDN)
    dn = u.astimezone(_NY)
    ds = u.astimezone(_SYD)
    utc_hour = u.hour + u.minute / 60.0

    sessions_def = [
        {"name": "Sydney",   "open": _utc_hour_of(ds, _SYD, 8),  "close": _utc_hour_of(ds, _SYD, 17), "duration": 9.0, "color": "#00bfff"},
        {"name": "Tokyo",    "open": 0.0,                        "close": 9.0,                        "duration": 9.0, "color": "#ff6b9d"},
        {"name": "London",   "open": _utc_hour_of(dl, _LDN, 8),  "close": _utc_hour_of(dl, _LDN, 16), "duration": 8.0, "color": "#9d00ff"},
        {"name": "New York", "open": _utc_hour_of(dn, _NY, 8),   "close": _utc_hour_of(dn, _NY, 16),  "duration": 8.0, "color": "#00ff66"},
    ]
    result = []
    for s in sessions_def:
        o, c, dur = s["open"], s["close"], s["duration"]
        if o > c:  # wraps midnight UTC
            active = utc_hour >= o or utc_hour < c
            elapsed = (utc_hour - o) if utc_hour >= o else (utc_hour + 24.0 - o)
        else:
            active = o <= utc_hour < c
            elapsed = (utc_hour - o) if active else 0.0
        progress = min(max(elapsed / dur, 0.0), 1.0) if active else 0.0
        remaining_h = max(dur - elapsed, 0.0) if active else 0.0
        result.append({
            "name": s["name"],
            "active": active,
            "open_utc": round(o, 3),
            "close_utc": round(c, 3),
            "progress": round(progress, 3),
            "remaining_min": round(remaining_h * 60),
            "color": s["color"],
        })
    return result


# Map the internal classifier buckets to the human labels the dashboard/journal
# use when tagging which session a trade happened in.
_SESSION_LABELS = {
    "overlap": "Overlap",
    "london": "London",
    "newyork": "New York",
    "rollover": "Rollover",
    "asian": "Sydney/Tokyo",
}


def session_label(dt: datetime) -> str:
    """Human session label for a trade instant (dashboard/analysis). Derived from
    the same classifier as everything else — replaces the old machine-local-clock
    version that mislabelled trades whenever the host box was not on UTC."""
    return _SESSION_LABELS[classify_session(dt)[0]]
