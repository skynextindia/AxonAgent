"""AxonAI isolated Risk Engine prototype (SHADOW / RESEARCH ONLY).

This package is a PURE-CALCULATION research prototype. It computes *hypothetical*
position-sizing / risk decisions and NEVER touches the live trading path.

HARD ISOLATION GUARANTEES (verified 2026-08-18):
  * Imports NOTHING from ``axonai`` — every production formula it needs is
    re-implemented locally, with the source file:line it mirrors cited in a
    comment, so the running daemon/executor/RiskGuard cannot be imported,
    mutated, or side-effected by anything here.
  * Calls NO MT5 API, NO execution_bridge, NO trade_executor send/close,
    NO RiskGuard flatten, NO config mutation, NO restart.
  * ``research/`` is NOT on any production import path (grep-verified: no
    production module does ``import research`` / ``from research``).

Nothing in this package is wired into live execution. It is a bench.
"""

__all__ = ["models", "correlation", "risk_engine", "telemetry", "simulator"]
