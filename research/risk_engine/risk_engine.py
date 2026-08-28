"""Isolated Risk Engine — PURE calculation. No execution, ever.

Signal -> Candidate Engine -> [RiskEngine.decide] -> hypothetical size -> telemetry.
                                     |
                    equity / stop / daily-loss / floor-distance /
                    open-risk / correlated-exposure  (all INPUTS)

The engine takes a ``RiskState`` + a ``RiskPolicy`` and returns a ``RiskDecision``.
It calls NO MT5 API, mutates NO config, closes NO position. Given identical inputs
it returns an identical output (deterministic).

IMPORTANT — no baked-in risk thresholds. Every throttle (floor / correlation /
daily) defaults to ``"off"`` (report-only, scale 1.0). No 0.6% / 0.4% / 0.9% or
any other level is hard-coded as a recommendation. A caller who wants a throttle
must pass its parameters explicitly. This is a bench, not a tuned strategy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, List

from .models import RiskState, RiskDecision, PropProfile, NODE_PROFILE
from .correlation import aggregate_usd_bucket


@dataclass
class RiskPolicy:
    """Externally-configured risk policy. NOTHING here is a recommendation.

    ``risk_mode``:
        "fixed_usd" -> base risk is a fixed dollar budget (the CURRENT node: $1,100,
                       equity-invariant). ``fixed_usd`` required.
        "pct"       -> base risk is ``base_risk_pct`` of equity. Caller MUST supply
                       the pct; there is no default 'good' value.

    Scale modes all default OFF (scale = 1.0):
        floor_mode  : "off" | "linear_taper" | "hard_block"
        corr_mode   : "off" | "shared_unit"  | "cap"
        daily_mode  : "off" | "linear_taper"
    """
    name: str = "unnamed"
    risk_mode: str = "pct"                       # "pct" | "fixed_usd"
    base_risk_pct: Optional[float] = None        # required when risk_mode="pct"
    fixed_usd: Optional[float] = None            # required when risk_mode="fixed_usd"

    # floor-aware throttle (keys off live distance-to-buffered-floor / equity)
    floor_mode: str = "off"
    floor_taper_start_frac: float = 0.06         # begin tapering when cushion < this frac of equity
    floor_taper_end_frac: float = 0.00           # fully throttled at this frac
    floor_min_scale: float = 0.0                 # scale floor at the throttle end

    # correlation throttle (EURUSD+USDJPY USD bucket)
    corr_mode: str = "off"
    corr_cap_pct: float = 0.0                    # cap same-direction bucket risk to this % equity ("cap")
    corr_shared_scale: float = 0.5               # size each correlated leg by this ("shared_unit")

    # daily-loss throttle (keys off current_daily_loss_pct vs buffered daily limit)
    daily_mode: str = "off"
    daily_taper_start_frac: float = 0.5          # begin tapering at this frac of the buffered daily limit
    daily_min_scale: float = 0.0

    # hard blocks (all default OFF so no threshold is imposed by default)
    block_if_projected_breach: bool = False      # block if a full stop would cross the buffered floor

    def validate(self) -> List[str]:
        errs = []
        if self.risk_mode == "pct" and not self.base_risk_pct:
            errs.append("risk_mode='pct' requires base_risk_pct")
        if self.risk_mode == "fixed_usd" and not self.fixed_usd:
            errs.append("risk_mode='fixed_usd' requires fixed_usd")
        if self.risk_mode not in ("pct", "fixed_usd"):
            errs.append(f"unknown risk_mode {self.risk_mode!r}")
        return errs


def _round_to_step(lot: float, step: float) -> float:
    if step and step > 0:
        return round(math.floor(lot / step + 1e-9) * step, 2)
    return round(lot, 2)


def _floor_scale(state: RiskState, policy: RiskPolicy, warnings: List[str]) -> float:
    if policy.floor_mode == "off":
        return 1.0
    dist = state.distance_to_buffered_floor
    eq = state.equity
    if dist is None or eq in (None, 0):
        warnings.append("floor throttle requested but distance/equity UNAVAILABLE -> scale 1.0")
        return 1.0
    cushion_frac = dist / eq
    if policy.floor_mode == "hard_block":
        # binary: full size while cushion above start, else 0
        return 1.0 if cushion_frac >= policy.floor_taper_start_frac else 0.0
    # linear_taper: 1.0 above start, floor_min_scale at/below end, linear between
    hi, lo = policy.floor_taper_start_frac, policy.floor_taper_end_frac
    if cushion_frac >= hi:
        return 1.0
    if cushion_frac <= lo:
        return policy.floor_min_scale
    t = (cushion_frac - lo) / (hi - lo)          # 0..1
    return policy.floor_min_scale + t * (1.0 - policy.floor_min_scale)


def _daily_scale(state: RiskState, policy: RiskPolicy, profile: PropProfile,
                 warnings: List[str]) -> float:
    if policy.daily_mode == "off":
        return 1.0
    used = state.current_daily_loss_pct
    if used is None:
        warnings.append("daily throttle requested but current_daily_loss_pct UNAVAILABLE -> scale 1.0")
        return 1.0
    limit = profile.buffered_daily_limit_pct()
    if limit <= 0:
        return 1.0
    frac = max(0.0, used) / limit                # 0..1+ of the buffered daily limit consumed
    start = policy.daily_taper_start_frac
    if frac <= start:
        return 1.0
    if frac >= 1.0:
        return policy.daily_min_scale
    t = (frac - start) / (1.0 - start)
    return 1.0 - t * (1.0 - policy.daily_min_scale)


def _corr_scale(state: RiskState, policy: RiskPolicy, base_risk_usd: float,
                stop_pips: float, pip_value: float, warnings: List[str]):
    """Return (scale, correlated_exposure_notional)."""
    # Always compute the bucket exposure (for telemetry) even in 'off' mode.
    cand_lot_est = base_risk_usd / (stop_pips * pip_value) if stop_pips and pip_value else 0.0
    exp = aggregate_usd_bucket(
        positions=state.existing_positions,
        candidate_symbol=state.symbol,
        candidate_direction=state.direction,
        candidate_lot=cand_lot_est,
        candidate_price=state.entry_price or 0.0,
        candidate_risk_usd=base_risk_usd,
    )
    if policy.corr_mode == "off":
        return 1.0, exp.net_notional
    if policy.corr_mode == "shared_unit":
        # If any already-open bucket leg shares the candidate's USD sign, size the
        # candidate as a fraction (treat the correlated pair as ~one unit).
        has_same_dir_open = exp.n_bucket_positions > 0 and exp.same_dir_risk_usd > base_risk_usd + 1e-9
        return (policy.corr_shared_scale if has_same_dir_open else 1.0), exp.net_notional
    if policy.corr_mode == "cap":
        eq = state.equity or 0.0
        cap_usd = eq * (policy.corr_cap_pct / 100.0)
        if cap_usd <= 0 or exp.same_dir_risk_usd <= cap_usd:
            return 1.0, exp.net_notional
        # scale candidate risk down so total same-direction risk == cap
        already = exp.same_dir_risk_usd - base_risk_usd
        allowed_cand = max(0.0, cap_usd - already)
        return (allowed_cand / base_risk_usd if base_risk_usd > 0 else 0.0), exp.net_notional
    warnings.append(f"unknown corr_mode {policy.corr_mode!r} -> scale 1.0")
    return 1.0, exp.net_notional


def decide(state: RiskState, policy: RiskPolicy,
           profile: PropProfile = NODE_PROFILE) -> RiskDecision:
    """Pure risk decision. Deterministic. No side effects."""
    d = RiskDecision(base_risk=policy.base_risk_pct)
    w = d.warnings

    perr = policy.validate()
    if perr:
        d.allowed = False
        d.decision_reason = "invalid policy: " + "; ".join(perr)
        return d

    # ── input validation (do not invent missing values) ────────────────────
    for m in state.missing():
        w.append(f"input UNAVAILABLE: {m}")
    if state.equity is None or state.equity <= 0:
        d.allowed = False
        d.decision_reason = "equity UNAVAILABLE or <= 0 — cannot size"
        return d
    if state.stop_distance_pips is None or state.stop_distance_pips <= 0:
        d.allowed = False
        d.decision_reason = "stop_distance_pips UNAVAILABLE or <= 0 — cannot size"
        return d
    if state.pip_value is None or state.pip_value <= 0:
        d.allowed = False
        d.decision_reason = "pip_value UNAVAILABLE or <= 0 — cannot size"
        return d

    eq = state.equity
    stop_pips = state.stop_distance_pips
    pip_value = state.pip_value

    # already at/below the buffered floor -> never add risk
    if state.distance_to_buffered_floor is not None and state.distance_to_buffered_floor <= 0:
        d.allowed = False
        d.decision_reason = "buffered floor already reached — no new risk"
        d.correlated_exposure = None
        return d

    # ── base risk ───────────────────────────────────────────────────────────
    if policy.risk_mode == "fixed_usd":
        base_risk_usd = float(policy.fixed_usd)
        d.base_risk = base_risk_usd / eq
    else:
        base_risk_usd = eq * float(policy.base_risk_pct)
        d.base_risk = float(policy.base_risk_pct)

    # ── scales (each defaults to 1.0 / report-only) ──────────────────────────
    d.floor_scale = _floor_scale(state, policy, w)
    d.daily_loss_scale = _daily_scale(state, policy, profile, w)
    d.correlation_scale, corr_exposure = _corr_scale(
        state, policy, base_risk_usd, stop_pips, pip_value, w)
    d.correlated_exposure = corr_exposure

    d.final_scale = d.floor_scale * d.correlation_scale * d.daily_loss_scale
    final_risk_usd = base_risk_usd * d.final_scale

    # scale drove the risk budget to (essentially) zero -> nothing to size
    if final_risk_usd <= 0.0:
        d.allowed = False
        d.decision_reason = "computed risk collapsed to 0 (scale drove size to zero)"
        d.lot_size = 0.0
        d.risk_usd = 0.0
        d.risk_pct = 0.0
        return d

    # ── lot from risk ─────────────────────────────────────────────────────────
    # Order mirrors production trade_executor: apply the [min_lot, max_lot] bounds
    # to the risk-derived lot FIRST (executor lines 221/233), then snap to the
    # broker volume step (lines 320-324). min/max before step, never after.
    raw_lot = final_risk_usd / (stop_pips * pip_value)
    lot = raw_lot
    min_lot_bump = False
    if lot > state.max_lot:
        lot = state.max_lot
        w.append(f"max_lot cap {state.max_lot} reduces risk below target")
    if lot < state.min_lot:
        lot = state.min_lot
        min_lot_bump = True
        w.append(f"min_lot floor {state.min_lot} RAISES risk above target")
    lot = _round_to_step(lot, state.lot_step)
    if lot < state.min_lot:                 # step-snap must never fall below min_lot
        lot = round(state.min_lot, 2)

    # realized risk after clamps
    realized_risk_usd = lot * stop_pips * pip_value
    d.lot_size = lot
    d.risk_usd = realized_risk_usd
    d.risk_pct = realized_risk_usd / eq
    d.projected_risk_pct = d.risk_pct

    # projected buffered-floor distance if this trade takes a full stop
    if state.distance_to_buffered_floor is not None:
        d.projected_floor_distance = state.distance_to_buffered_floor - realized_risk_usd

    # ── hard block: projected breach (only if caller opted in) ───────────────
    if (policy.block_if_projected_breach and d.projected_floor_distance is not None
            and d.projected_floor_distance < 0):
        d.allowed = False
        d.decision_reason = (f"BLOCK: a full stop would cross the buffered floor "
                             f"(projected cushion {d.projected_floor_distance:.0f})")
        return d

    d.allowed = True
    bits = [f"{policy.risk_mode} base {d.base_risk*100:.2f}%"]
    if d.floor_scale != 1.0:
        bits.append(f"floor×{d.floor_scale:.2f}")
    if d.correlation_scale != 1.0:
        bits.append(f"corr×{d.correlation_scale:.2f}")
    if d.daily_loss_scale != 1.0:
        bits.append(f"daily×{d.daily_loss_scale:.2f}")
    if min_lot_bump:
        bits.append("min-lot-bumped")
    bits.append(f"-> {lot:.2f} lot, {d.risk_pct*100:.2f}% (${realized_risk_usd:.0f})")
    d.decision_reason = "; ".join(bits)
    return d
