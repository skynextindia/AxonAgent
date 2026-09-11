"""Direction + Trade-Location Forensics — ISOLATED research module (READ-ONLY).

Measurement, not modification. This package reconstructs and classifies the
FULL available lead + node trade history to answer, per trade:

    1. Wrong direction?
    2. Correct direction but bad location?
    3. Correct direction/location but bad timing?
    4. Correct trade but bad exit?
    5. RiskGuard / risk-state distortion?

Hard isolation guarantees (mirrors research/risk_engine):
  * Imports NOTHING from ``axonai``; no MetaTrader5; no execution/order/close call.
  * Opens the production journals (reports/signals*.jsonl) STRICTLY read-only and
    writes NOTHING back to them. All outputs go under this package's ``out/`` dir
    (and the 5 markdown reports in the package root).
  * Builds NO BUY/SELL algorithm, adds NO filter, changes NO signal logic. It only
    classifies trades that already happened.
  * Never invents a missing field — absent inputs are marked UNAVAILABLE (None).

Entry point:  ``python -m research.direction_location_forensics.run_forensics``
"""
