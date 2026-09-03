"""Good-spot selector — the redesign's regime-aware entry decision, as ONE pure function
so the read-only shadow and the eventual live gate call the SAME validated code.

Given a fade direction (from the exhaustion peak) and the live MTF stamp, decide whether
the fade is a GOOD SPOT to TAKE, a spot to SKIP, or one to FLIP to the with-trend side.

Rules (from mtf_cross_regime_spots.py, the +1.11p good-spot map; HTF trend = the net/range
label from redesign step 1):
  * HTF RANGE            -> TAKE  (fade the range — the default good bucket)
  * HTF DOWN + Sell fade -> TAKE  (sell a rally in a downtrend — the best cell, +2.04p)
  * HTF UP   + Buy  fade -> TAKE  (buy a dip in an uptrend)
  * HTF DOWN + Buy  fade -> counter-trend (buy into a downtrend = falling knife):
                            SKIP, or FLIP to Sell when flip_counter_trend
  * HTF UP   + Sell fade -> counter-trend (sell into an uptrend):
                            SKIP, or FLIP to Buy  when flip_counter_trend
  * no HTF read          -> TAKE  (fail-open; never block on missing data)

NEVER trades. The daemon stamps the verdict on each signal / wtms setup; the checkpoint
decides whether take/skip/flip actually beats blind fading before anything is armed.
"""
from __future__ import annotations
from typing import Optional


def htf_trend(mtf_stamp, key: str = "1D") -> Optional[str]:
    """Higher-TF trend label from the live stamp's tfs dict ([trend, pos, net_range, er])."""
    tfs = (mtf_stamp or {}).get("tfs") or {}
    v = tfs.get(key)
    if isinstance(v, (list, tuple)) and v:
        return str(v[0]).upper()
    return None


def good_spot_decision(fade_dir, mtf_stamp, htf_key: str = "1D",
                       flip_counter_trend: bool = False) -> dict:
    """Return {action, reason, htf, htf_key[, flip_to]}. action in {take, skip, flip}."""
    fd = "Buy" if str(fade_dir).strip().lower().startswith("b") else "Sell"
    htf = htf_trend(mtf_stamp, htf_key)
    base = {"htf": htf, "htf_key": htf_key, "fade_dir": fd}
    if htf not in ("UP", "DOWN", "RANGE"):
        return {**base, "action": "take", "reason": "no HTF read (fail-open)"}
    if htf == "RANGE":
        return {**base, "action": "take", "reason": "fade in range"}
    aligned = (htf == "DOWN" and fd == "Sell") or (htf == "UP" and fd == "Buy")
    if aligned:
        return {**base, "action": "take", "reason": f"{fd} fade aligned to HTF {htf}"}
    # counter-trend fade: Buy into a DOWN htf, or Sell into an UP htf
    if flip_counter_trend:
        flip = "Sell" if fd == "Buy" else "Buy"
        return {**base, "action": "flip", "flip_to": flip,
                "reason": f"{fd} fade counter to HTF {htf} -> flip to {flip}"}
    return {**base, "action": "skip", "reason": f"{fd} fade counter to HTF {htf}"}
