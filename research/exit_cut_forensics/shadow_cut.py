"""SHADOW "level broke -> cut" evaluator — logs would-cut per LIVE trade, changes
NOTHING.

Design contract (mirrors research/risk_engine/live_observer.py):
  * Pure + self-contained: imports NOTHING from axonai/ or MT5. Takes only
    primitives (floats/strings/ints), returns a plain dict or None.
  * INCAPABLE of mutating live objects: it never receives a position, order, or
    daemon object — only numbers. It cannot send, modify, or close anything.
  * Writes ONLY to research/exit_cut_forensics/shadow_out/would_cut_shadow.jsonl.
  * Never raises into the caller (all writes are best-effort, guarded).
  * Off by default at the call site (the daemon hook is flag-gated
    ``shadow_cut_enabled=False``); this class is also constructed with enabled
    controllable.

What it records (once per ticket, the first time the faded level "breaks"):
  the pips at which a cut WOULD have exited (``-(level_dist+buffer)``), the live
  adverse excursion at that moment, and the geometry — so a checkpoint can compare
  the would-cut exit against the trade's ACTUAL realized pips (from the close log)
  and confirm the forensic's +net on live, out-of-sample data BEFORE anything arms.

This does not decide direction and does not touch the exit. It only watches.
"""

from __future__ import annotations

import os
import json
from typing import Dict, Optional, Any

SHADOW_ONLY = True

_THIS = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_OUT = os.path.join(_THIS, "shadow_out")


def _pip_size(symbol: str) -> float:
    s = (symbol or "").upper()
    return 0.01 if ("JPY" in s or "XAU" in s) else 0.0001


def adverse_side_dist_pips(direction: str, entry: float, sr_level: float,
                           pip: float) -> Optional[float]:
    """Distance (pips) to the faded level IF it sits on the adverse side of the
    entry (resistance above a SELL / support below a BUY). Else None => the cut
    rule does not apply to this geometry (e.g. sell-into-support)."""
    if direction not in ("BUY", "SELL") or entry is None or sr_level is None:
        return None
    delta = (sr_level - entry) / pip          # +ve => level above entry
    if direction == "SELL":
        return delta if delta >= 0 else None
    return -delta if delta <= 0 else None      # BUY: support below


class ShadowCutTracker:
    """Per-ticket MAE watcher. One instance lives on the daemon; the daemon calls
    ``observe(...)`` per tick per open position and ``forget(ticket)`` on close."""

    def __init__(self, buffer_pips: float = 3.0, enabled: bool = True,
                 out_dir: Optional[str] = None):
        self.buffer_pips = float(buffer_pips)
        self.enabled = bool(enabled)
        self.out_dir = out_dir or _DEFAULT_OUT
        self._st: Dict[int, Dict[str, Any]] = {}

    # -- lifecycle -----------------------------------------------------------
    def forget(self, ticket: int) -> None:
        self._st.pop(int(ticket), None)

    # -- per-tick observation ------------------------------------------------
    def observe(self, *, ticket: int, direction: str, entry: float,
                sr_level: Optional[float], symbol: str, bid: float, ask: float,
                sl_pips: Optional[float], account: str = "lead",
                epoch: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Advance the ticket's live adverse excursion; if the faded level breaks
        by ``buffer_pips`` for the first time, record + return the would-cut row.
        Returns None on every tick that does not newly fire. Never raises."""
        if not self.enabled:
            return None
        try:
            tk = int(ticket)
            st = self._st.get(tk)
            if st is None:
                pip = _pip_size(symbol)
                dist = adverse_side_dist_pips(direction, entry, sr_level, pip)
                st = self._st[tk] = {
                    "pip": pip, "dist": dist, "entry": entry, "dir": direction,
                    "symbol": symbol, "sl_pips": sl_pips, "max_mae": 0.0,
                    "fired": False, "na": dist is None,
                }
            if st["fired"] or st["na"]:
                # still track MAE for context even after firing / N/A
                self._advance_mae(st, direction, entry, bid, ask)
                return None
            adverse = self._advance_mae(st, direction, entry, bid, ask)
            dist = st["dist"]
            thresh = dist + self.buffer_pips
            # If the hard stop is tighter than the break threshold, price hits the
            # stop first — the cut can never trigger; mark N/A so we stop checking.
            if sl_pips is not None and thresh > float(sl_pips) + 1e-9:
                st["na"] = True
                return None
            if adverse + 1e-9 < thresh:
                return None                      # level not yet broken
            # FIRE (once)
            st["fired"] = True
            row = {
                "type": "would_cut", "account": account, "ticket": tk,
                "symbol": symbol, "direction": direction,
                "entry": round(entry, 5),
                "faded_level": round(sr_level, 5) if sr_level is not None else None,
                "level_dist_pips": round(dist, 2),
                "buffer_pips": self.buffer_pips,
                "would_cut_pips": round(-(dist + self.buffer_pips), 2),
                "adverse_at_fire_pips": round(adverse, 2),
                "sl_pips": round(float(sl_pips), 1) if sl_pips is not None else None,
                "epoch": int(epoch) if epoch is not None else None,
            }
            self._write(row)
            return row
        except Exception:
            # Shadow must never disturb the live loop.
            return None

    # -- internals -----------------------------------------------------------
    @staticmethod
    def _advance_mae(st: Dict[str, Any], direction: str, entry: float,
                     bid: float, ask: float) -> float:
        pip = st["pip"]
        if direction == "SELL":
            adverse = (ask - entry) / pip
        else:
            adverse = (entry - bid) / pip
        if adverse > st["max_mae"]:
            st["max_mae"] = adverse
        return st["max_mae"]

    def _write(self, row: Dict[str, Any]) -> None:
        try:
            os.makedirs(self.out_dir, exist_ok=True)
            with open(os.path.join(self.out_dir, "would_cut_shadow.jsonl"),
                      "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except Exception:
            pass  # best-effort; a failed shadow write must not affect anything
