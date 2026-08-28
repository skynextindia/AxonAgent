"""Top-down multi-timeframe STRUCTURE (read-only research).

Gives the machine what it's currently blind to: at any moment, the trend
(UP/DOWN/RANGE) and price's position within the range at every scale from
multi-year down to intraday. Pure calculation — no live wiring here; a sibling
design doc covers how to feed it live. Never imports axonai/MT5.
"""
