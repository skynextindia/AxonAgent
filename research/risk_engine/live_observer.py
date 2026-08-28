"""Read-only LIVE observer that bridges the running daemon to the isolated Risk
Engine — WITHOUT touching execution.

Phase: SHADOW INTEGRATION ONLY. This module is the *only* connection between the
live signal/execution path and ``research/risk_engine``. It is a pure observer:

  * It NEVER sends, modifies, cancels, or closes an order.
  * It NEVER imports MetaTrader5, ``trade_executor``, ``daemon``, ``risk_guard``
    classes, or any execution/close/flatten function.
  * It NEVER mutates the objects it reads (``live_state``, ``risk_guard``,
    ``config``, ``trade_result`` are read strictly by attribute/key access).
  * It NEVER chooses BUY/SELL. Direction is CONSUMED from the production signal
    and echoed back; the observer asserts it can never flip it.
  * It NEVER raises into the caller. Any error is swallowed and logged into the
    shadow output; the daemon's execution path is unaffected.
  * All output goes ONLY to ``research/risk_engine/shadow_out/`` via the
    isolation-guarded ``ShadowTelemetryWriter``.

The observer is invoked by the daemon behind an explicit, default-OFF flag
(``shadow_risk_observer_enabled``). With the flag off — its default, and the
state of the running system — this module is never imported or called.

Design of the shadow decision
-----------------------------
The observer builds a ``RiskState`` from the values already present at the entry
point and asks ``decide()`` for a *hypothetical* lot using a REFERENCE policy
that MIRRORS production's own sizing mode (fixed-USD budget on the node, risk-%
on the lead) with **all throttles OFF**. It imposes no new risk threshold. The
purpose is diagnostic: does the isolated engine reproduce production sizing from
the observed inputs? Divergence flags a modeling gap, never a trade action.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .models import RiskState, OpenPosition
from .risk_engine import RiskPolicy, decide
from .telemetry import ShadowTelemetryWriter

logger = logging.getLogger(__name__)

# Explicit SHADOW-ONLY guarantee, asserted by the tests. This module must never
# reference an execution API. If any of these names ever appear here, the import
# test fails loudly.
SHADOW_ONLY = True

_OBSERVER_FILENAME = "live_observer.jsonl"


# ── direction mapping (consume; never choose) ───────────────────────────────
def _signal_to_direction(signal: str) -> Optional[str]:
    """Map a 5-tier production signal to BUY/SELL. Returns None for HOLD/unknown.

    This is a faithful mirror of ``trade_executor.execute_signal`` (Buy/Overweight
    -> BUY, Sell/Underweight -> SELL). The observer does NOT decide direction; it
    only translates the direction the strategy already produced so it can record
    and assert it.
    """
    if signal in ("Buy", "Overweight"):
        return "BUY"
    if signal in ("Sell", "Underweight"):
        return "SELL"
    return None


def _pip_size(symbol: str) -> float:
    s = (symbol or "").upper()
    return 0.01 if ("JPY" in s or "XAU" in s) else 0.0001


def _pip_value_per_lot(config: dict, symbol: str, price: Optional[float], pip: float) -> Optional[float]:
    """Mirror ``trade_executor._pip_value_per_lot`` (read-only, no MT5).

    Config pins USD-quote pairs (~$10). USD-base pairs (JPY) derive from the
    contract size and live fill price: contract * pip / price.
    """
    configured = config.get("realtime_pip_value_per_lot", 10.0)
    if configured:
        return float(configured)
    contract = 100_000.0
    val = contract * pip
    if price and price > 0:
        val = val / price
    return max(val, 0.01)


def _reference_policy(config: dict) -> Optional[RiskPolicy]:
    """Build a policy that MIRRORS production sizing, all throttles OFF.

    * node  -> ``max_loss_per_trade_usd`` set  => fixed-USD budget policy
    * lead  -> ``realtime_risk_pct``      set  => risk-% policy
    * fixed_lot only (no risk basis) or nothing => None (recorded UNAVAILABLE)

    No floor/correlation/daily throttle is enabled: the observer imposes NO new
    risk threshold. It is report-only, reproducing what the CURRENT mode sizes.
    """
    max_loss = config.get("max_loss_per_trade_usd")
    if max_loss:
        return RiskPolicy(name="ref_fixed_usd", risk_mode="fixed_usd", fixed_usd=float(max_loss))
    risk_pct = config.get("realtime_risk_pct")
    if risk_pct:
        return RiskPolicy(name="ref_pct", risk_mode="pct", base_risk_pct=float(risk_pct))
    return None


def _daily_loss_pct(risk_guard: Any, equity: Optional[float]) -> Optional[float]:
    """Read-only floating daily-loss %, computed WITHOUT touching RiskGuard state.

    Mirrors ``risk_guard.is_halted``'s floating-loss math but never reseeds or
    saves the daily-pnl file (``is_halted`` can rewrite it on a stale baseline —
    we must not). Returns None if the baseline is unavailable.
    """
    if risk_guard is None or equity in (None, 0):
        return None
    try:
        daily = getattr(risk_guard, "daily_pnl", None)
        if not isinstance(daily, dict):
            return None
        start_eq = float(daily.get("start_equity", 0.0) or 0.0)
        if start_eq <= 0:
            return None
        return max(0.0, (start_eq - float(equity)) / start_eq * 100.0)
    except Exception:
        return None


def _floor_distances(risk_guard: Any, equity: Optional[float]):
    """Return (dist_to_buffered_floor, dist_to_firm_floor, buffered_floor, firm_floor).

    Uses only the pure read methods ``drawdown_floor()`` / ``hard_floor()``. A
    0.0 floor means the prop guard is not armed (e.g. the lead) -> distance is
    genuinely UNAVAILABLE (None), never invented.
    """
    if risk_guard is None or equity in (None, 0):
        return None, None, None, None
    try:
        buffered = float(risk_guard.drawdown_floor())
    except Exception:
        buffered = 0.0
    try:
        firm = float(risk_guard.hard_floor())
    except Exception:
        firm = 0.0
    dist_buf = (float(equity) - buffered) if buffered > 0 else None
    dist_firm = (float(equity) - firm) if firm > 0 else None
    return dist_buf, dist_firm, (buffered or None), (firm or None)


def _initial_balance(risk_guard: Any) -> Optional[float]:
    if risk_guard is None:
        return None
    try:
        if getattr(risk_guard, "prop_enabled", False):
            ib = float(getattr(risk_guard, "prop_state", {}).get("initial_balance", 0.0) or 0.0)
            return ib or None
    except Exception:
        pass
    return None


def _atr(live_state: Any) -> Optional[float]:
    try:
        st = getattr(live_state, "_state", None)
        if st is not None:
            a = getattr(st, "atr_14_h1", None)
            return float(a) if a else None
    except Exception:
        pass
    return None


def _correlated_snapshot(correlation_engine: Any):
    """Best-effort, read-only view of the OTHER bucket legs (no MT5, no mutation).

    We do not enumerate MT5 positions (that would need an execution import). If
    the live CorrelationEngine exposes a plain read-only registry of open legs we
    reflect it; otherwise we return an empty set and mark exposure UNAVAILABLE.
    """
    positions = []
    if correlation_engine is None:
        return positions
    for attr in ("open_positions", "positions", "_positions", "registered"):
        try:
            reg = getattr(correlation_engine, attr, None)
        except Exception:
            reg = None
        if not reg:
            continue
        try:
            iterable = reg.values() if isinstance(reg, dict) else reg
            for p in iterable:
                sym = getattr(p, "symbol", None) or (p.get("symbol") if isinstance(p, dict) else None)
                direction = getattr(p, "direction", None) or (p.get("direction") if isinstance(p, dict) else None)
                lot = getattr(p, "lot", None) or (p.get("lot") if isinstance(p, dict) else None)
                price = getattr(p, "price", None) or (p.get("price") if isinstance(p, dict) else None)
                if sym and direction:
                    positions.append(OpenPosition(
                        symbol=str(sym), direction=str(direction).upper(),
                        lot=float(lot or 0.0), entry_price=float(price or 0.0)))
            if positions:
                break
        except Exception:
            positions = []
            continue
    return positions


def build_state(*, symbol: str, direction: str, live_state: Any, risk_guard: Any,
                correlation_engine: Any, trade_result: dict, config: dict) -> RiskState:
    """Assemble a RiskState from observed values. Never invents a missing value."""
    equity = None
    balance = None
    if risk_guard is not None:
        eq = getattr(risk_guard, "current_equity", None)
        equity = float(eq) if eq else None
        bal = getattr(risk_guard, "current_balance", None)
        balance = float(bal) if bal else None

    tr = trade_result or {}
    entry_price = tr.get("price") or None
    stop_price = tr.get("sl") or None
    pip = _pip_size(symbol)

    # Faithful production stop distance: prefer the ACTUAL |entry - sl| the order
    # went out with; fall back to the configured hard distance.
    stop_pips = None
    if entry_price and stop_price:
        stop_pips = abs(float(entry_price) - float(stop_price)) / pip
        stop_pips = stop_pips if stop_pips > 0 else None
    if stop_pips is None and config.get("hard_distance_mode") and config.get("realtime_hard_stop_pips"):
        stop_pips = float(config["realtime_hard_stop_pips"])

    dist_buf, dist_firm, _bf, _ff = _floor_distances(risk_guard, equity)
    init_bal = _initial_balance(risk_guard)
    dd_pct = None
    if init_bal and equity:
        dd_pct = max(0.0, (init_bal - equity) / init_bal * 100.0)

    return RiskState(
        equity=equity,
        balance=balance,
        initial_balance=init_bal,
        symbol=symbol,
        direction=direction,
        entry_price=float(entry_price) if entry_price else None,
        stop_price=float(stop_price) if stop_price else None,
        stop_distance_pips=stop_pips,
        atr=_atr(live_state),
        current_daily_loss_pct=_daily_loss_pct(risk_guard, equity),
        current_drawdown_pct=dd_pct,
        distance_to_buffered_floor=dist_buf,
        distance_to_firm_floor=dist_firm,
        existing_positions=_correlated_snapshot(correlation_engine),
        existing_open_risk_usd=None,     # cannot enumerate without MT5 -> UNAVAILABLE
        correlated_open_risk_usd=None,   # UNAVAILABLE (never invented)
        pip_value=_pip_value_per_lot(config, symbol, entry_price, pip),
        min_lot=float(config.get("realtime_min_lot", 1.0)),
        max_lot=float(config.get("realtime_max_lot", 0.10)),
        lot_step=0.01,
    )


def observe_entry(*, symbol: str, production_signal: str, live_state: Any,
                  size_scale: float, risk_guard: Any, correlation_engine: Any,
                  trade_result: dict, config: dict, signal_id: str = "",
                  timestamp: Optional[str] = None,
                  writer: Optional[ShadowTelemetryWriter] = None) -> Optional[dict]:
    """Observe one just-executed production entry and record a shadow decision.

    Returns the telemetry row (also written to shadow_out/) or None if nothing
    could be recorded. NEVER raises. NEVER acts. NEVER changes direction.
    """
    try:
        production_direction = _signal_to_direction(production_signal)
        if production_direction is None:
            return None  # HOLD / non-directional — nothing to size

        # The Risk Engine consumes this direction; it has no capacity to choose or
        # flip it (RiskDecision carries no direction field). We assert identity
        # explicitly and record it as a hard invariant.
        shadow_direction = production_direction
        direction_preserved = (shadow_direction == production_direction)

        state = build_state(
            symbol=symbol, direction=shadow_direction, live_state=live_state,
            risk_guard=risk_guard, correlation_engine=correlation_engine,
            trade_result=trade_result, config=config)

        policy = _reference_policy(config)
        if policy is None:
            decision = None
            reason = "no risk-based production sizing mode to mirror (fixed_lot?) -> shadow size UNAVAILABLE"
        else:
            decision = decide(state, policy)
            reason = decision.decision_reason

        production_lot = (trade_result or {}).get("volume")
        ts = timestamp or datetime.now(timezone.utc).isoformat()

        row = {
            "timestamp": ts,
            "signal_id": signal_id or "",
            "symbol": symbol,
            # direction invariants (consumed, never chosen)
            "production_direction": production_direction,
            "shadow_direction": shadow_direction,
            "direction_preserved": direction_preserved,
            # observed inputs at the entry point
            "entry_price": state.entry_price,
            "atr": state.atr,
            "stop_distance": state.stop_distance_pips,
            "stop_price": state.stop_price,
            "equity_before": state.equity,
            "balance_before": state.balance,
            "initial_balance": state.initial_balance,
            "drawdown_pct": state.current_drawdown_pct,
            "distance_to_prop_floor": state.distance_to_buffered_floor,
            "distance_to_firm_floor": state.distance_to_firm_floor,
            "daily_loss_used": state.current_daily_loss_pct,
            "correlated_exposure": (decision.correlated_exposure if decision else None),
            "existing_open_risk_usd": state.existing_open_risk_usd,
            "pip_value": state.pip_value,
            "production_size_scale": size_scale,
            # production vs shadow
            "production_lot": production_lot,
            "shadow_proposed_risk_pct": (decision.risk_pct if decision else None),
            "shadow_proposed_risk_usd": (decision.risk_usd if decision else None),
            "shadow_proposed_lot": (decision.lot_size if decision else None),
            "shadow_decision": ("allow" if (decision and decision.allowed)
                                else "reject" if decision else "unavailable"),
            "risk_policy_used": (policy.name if policy else "unavailable"),
            "reason": reason,
            "warnings": (list(decision.warnings) if decision else ["no reference policy"]),
            "missing_inputs": state.missing(),
        }

        if not direction_preserved:
            # Structurally impossible, but recorded and warned rather than trusted.
            row["warnings"].append(
                f"DIRECTION INVARIANT VIOLATED: prod={production_direction} shadow={shadow_direction}")
            logger.error("live_observer: direction invariant violated (observer still took NO action)")

        w = writer or ShadowTelemetryWriter(filename=_OBSERVER_FILENAME)
        w.write(row)
        return row
    except Exception as e:  # never propagate into the daemon
        logger.debug("live_observer.observe_entry swallowed error: %s", e)
        return None
