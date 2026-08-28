"""Shadow telemetry for the Risk Engine prototype.

Writes hypothetical risk decisions to an ISOLATED research location:
    research/risk_engine/shadow_out/*.jsonl

It NEVER appends to the live production journals (reports/signals.jsonl,
reports/signals_node.jsonl) or any file the running system reads/writes. The
output dir is created on demand and is safe to delete.
"""

from __future__ import annotations

import os
import json
from dataclasses import dataclass
from typing import Optional, Dict, Any

from .models import RiskState, RiskDecision

# Isolated output root (this file's dir / shadow_out). Resolved absolutely so the
# process CWD (the live daemon's CWD is the repo root) can never redirect it onto
# a production path.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SHADOW_OUT_DIR = os.path.join(_THIS_DIR, "shadow_out")

# Guard: refuse to ever write into the live reports/ journal dir.
_FORBIDDEN_SUBSTR = (os.path.join("reports", "signals"),)


def _assert_isolated(path: str) -> None:
    ap = os.path.abspath(path).replace("\\", "/").lower()
    for bad in _FORBIDDEN_SUBSTR:
        if bad.replace("\\", "/").lower() in ap:
            raise RuntimeError(f"telemetry refuses to write to a production path: {path}")
    if "/shadow_out/" not in ap and not ap.endswith("/shadow_out"):
        raise RuntimeError(f"telemetry only writes under shadow_out/, got: {path}")


def telemetry_row(state: RiskState, decision: RiskDecision, *,
                  timestamp: str = "", signal_id: str = "",
                  balance_before: Optional[float] = None) -> Dict[str, Any]:
    """Build the shadow telemetry record (spec field set)."""
    return {
        "timestamp": timestamp,
        "signal_id": signal_id,
        "symbol": state.symbol,
        "direction": state.direction,
        "equity_before": state.equity,
        "balance_before": balance_before if balance_before is not None else state.balance,
        "entry_price": state.entry_price,
        "stop_price": state.stop_price,
        "stop_pips": state.stop_distance_pips,
        "atr": state.atr,
        "base_risk_pct": decision.base_risk,
        "base_risk_usd": (state.equity * decision.base_risk)
                         if (state.equity and decision.base_risk) else None,
        "floor_scale": decision.floor_scale,
        "correlation_scale": decision.correlation_scale,
        "daily_loss_scale": decision.daily_loss_scale,
        "final_risk_pct": decision.risk_pct,
        "final_risk_usd": decision.risk_usd,
        "hypothetical_lot": decision.lot_size,
        "open_risk": state.existing_open_risk_usd,
        "correlated_risk": state.correlated_open_risk_usd,
        "floor_distance": state.distance_to_buffered_floor,
        "projected_floor_distance": decision.projected_floor_distance,
        "decision": "allow" if decision.allowed else "reject",
        "reason": decision.decision_reason,
        "warnings": list(decision.warnings),
    }


@dataclass
class ShadowTelemetryWriter:
    """Append-only JSONL writer, pinned to the isolated shadow_out/ dir."""
    filename: str = "risk_engine_shadow.jsonl"
    _path: str = ""

    def __post_init__(self):
        os.makedirs(SHADOW_OUT_DIR, exist_ok=True)
        self._path = os.path.join(SHADOW_OUT_DIR, self.filename)
        _assert_isolated(self._path)

    @property
    def path(self) -> str:
        return self._path

    def write(self, row: Dict[str, Any]) -> None:
        _assert_isolated(self._path)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    def write_decision(self, state: RiskState, decision: RiskDecision, **kw) -> None:
        self.write(telemetry_row(state, decision, **kw))
