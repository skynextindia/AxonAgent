"""Direction Forensics — ISOLATED research module (READ-ONLY / SHADOW ONLY).

Question this phase answers: do the EXISTING signal inputs (the features present
at the live decision point, daemon.py:1063-1068) contain a STABLE, out-of-sample
distinction between genuine reversal/exhaustion and continuation?

It reuses the sibling read-only loaders (research.direction_location_forensics)
to reconstruct labeled trades, then:
  * measures each decision-point feature's separation between clean winners and
    wrong-direction losses (stats.py),
  * tests the reversal-vs-continuation hypothesis explicitly,
  * runs a PURE SHADOW direction challenger (challenger.py) that can only output
    BUY/SELL/HOLD + reason codes and can never reach the executor,
  * validates chronologically (walkforward.py) with TRAIN/VALIDATION/OOS splits,
    refusing any rule that only helps the single August adverse regime.

Isolation guarantees (same as the sibling research packages):
  * Imports NOTHING from axonai; no MetaTrader5; no execution/order/close call.
  * Reads journals strictly read-only; writes ONLY under this package's out/.
  * Builds NO production BUY/SELL logic, adds NO live filter. Measurement only.
  * Never invents a missing feature — absent inputs stay UNAVAILABLE (None).

Entry point: ``python -m research.direction_forensics.run_direction_forensics``
"""
