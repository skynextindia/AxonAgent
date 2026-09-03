"""Good-spot selector — the redesign's regime-aware entry decision, as ONE pure function
so the read-only shadow and the eventual live gate call the SAME validated code.

Given a fade direction (from the exhaustion peak) and the live MTF stamp, decide whether
the fade is a GOOD SPOT to TAKE, a spot to SKIP, or one to FLIP to the with-trend side.

Rules (from mtf_cross_regime_spots.py + wrsf_backtest.py; HTF trend = the net/range label
from redesign step 1):
  * HTF RANGE            -> TAKE  (fade the range — +0.85p bucket)
  * HTF DOWN + Sell fade -> TAKE  (sell a rally in a downtrend — the best cell, +2.04p)
  * HTF UP   + Buy  fade -> SKIP when skip_up_buy (DEFAULT): the buy-dip-in-uptrend bucket
                            backtested -4.13p over 27k trades (the down/up ASYMMETRY). Set
                            skip_up_buy=False to take it (the old symmetric rule).
  * HTF DOWN + Buy  fade -> counter-trend (falling knife): SKIP, or FLIP to Sell if flip
  * HTF UP   + Sell fade -> counter-trend (sell into uptrend): SKIP, or FLIP to Buy if flip
  * no HTF read          -> TAKE  (fail-open; never block on missing data)

FLIP is FALSIFIED by the backtest (flip-counter WORSE than skip: +0.04 vs +0.43p @2D) —
kept only as a disabled option. The refined selector = RANGE-fade + sell-down-rallies,
skip everything else, which reproduces the ~+1.1p good-spots. In-sample; the live shadow
is the OOS gate. NEVER trades — the daemon stamps the verdict; the checkpoint decides.
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


def good_spot_decision(fade_dir, mtf_stamp, htf_key: str = "2D",
                       flip_counter_trend: bool = False, skip_up_buy: bool = True) -> dict:
    """Return {action, reason, htf, htf_key[, flip_to]}. action in {take, skip, flip}."""
    fd = "Buy" if str(fade_dir).strip().lower().startswith("b") else "Sell"
    htf = htf_trend(mtf_stamp, htf_key)
    base = {"htf": htf, "htf_key": htf_key, "fade_dir": fd}
    if htf not in ("UP", "DOWN", "RANGE"):
        return {**base, "action": "take", "reason": "no HTF read (fail-open)"}
    if htf == "RANGE":
        return {**base, "action": "take", "reason": "fade in range"}
    if htf == "DOWN" and fd == "Sell":
        return {**base, "action": "take", "reason": "sell rally in downtrend (aligned)"}
    if htf == "UP" and fd == "Buy":
        if skip_up_buy:
            return {**base, "action": "skip", "reason": "buy-dip in uptrend (falsified -4p bucket)"}
        return {**base, "action": "take", "reason": "buy dip in uptrend (aligned)"}
    # counter-trend fade: Buy into a DOWN htf, or Sell into an UP htf
    if flip_counter_trend:
        flip = "Sell" if fd == "Buy" else "Buy"
        return {**base, "action": "flip", "flip_to": flip,
                "reason": f"{fd} fade counter to HTF {htf} -> flip to {flip}"}
    return {**base, "action": "skip", "reason": f"{fd} fade counter to HTF {htf}"}
