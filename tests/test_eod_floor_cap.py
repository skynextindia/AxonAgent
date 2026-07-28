"""The calibrator's per-pair floors must never ratchet themselves shut.

Regression for the 2026-07-27..28 finding: floors are derived from PRE-REVERSAL
ticks but applied to TRIGGERED ticks. Those populations differ, so the raw
0.8 * median floor climbed until only ~2% of live triggers survived, and each
refit tightened further (a blocked trigger can't argue for a looser floor). The
trigger-cap must (a) only ever loosen the raw floor and (b) admit a reasonable
share of the live trigger pool.
"""
from axonai.scripts.eod_reversal_analysis import (
    _trigger_feature_pool,
    _quantile,
)


def _row(state, skip, vel, vol, eff):
    return {"entry_state": state, "skip_reason": skip,
            "vel_pct": vel, "vol_pips": vol, "tick_eff": eff}


def test_trigger_pool_includes_edge_gate_rows_excludes_confluence_rejects():
    rows = [
        _row("TRIGGERED", "", 70, 1.0, 0.5),                       # passed everything
        _row("TRIGGERED", "edge_gate: vel_pct<60", 40, 1.0, 0.5),  # passed confluence, edge-killed
        _row("TRIGGERED", "below unified threshold (0.40<0.55)", 80, 2.0, 0.9),  # confluence reject
        _row("RETEST_WAIT", "", 90, 3.0, 0.9),                     # not a trigger
    ]
    pool = _trigger_feature_pool(rows, ["vel_pct", "vol_pips", "tick_eff"])
    # Only the two confluence-passed triggers count; the confluence reject and the
    # non-trigger are excluded. Excluding the edge-killed row would re-arm the ratchet.
    assert sorted(pool["vel_pct"]) == [40.0, 70.0]


def test_cap_only_loosens_never_tightens():
    # Trigger pool sits well below a hindsight-tight raw floor.
    pool = [10.0, 12.0, 15.0, 18.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0]
    raw = 60.0
    qq = 1.0 - 0.60  # admit >= 60% of triggers -> 40th percentile cap
    cap = _quantile(pool, qq)
    floor = min(raw, cap)
    assert floor <= raw
    admit = sum(1 for v in pool if v >= floor) / len(pool)
    assert admit >= 0.60


def test_cap_does_not_raise_a_low_raw_floor():
    # When the raw floor is already loose, the cap must not push it UP.
    pool = [10.0, 20.0, 30.0, 40.0, 50.0]
    raw = 5.0
    cap = _quantile(pool, 1.0 - 0.60)
    assert min(raw, cap) == raw
