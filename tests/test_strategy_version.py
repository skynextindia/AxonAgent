"""Strategy-version stamp: every trade must carry a version fingerprint derived
from the active experiment flags, so phased-rollout trades are attributable."""
from axonai.default_config import strategy_version, STRATEGY_VERSION_BASE
from axonai.realtime.trade_analytics import TradeRecord


def test_baseline_all_flags_off():
    v = strategy_version({})
    assert v == f"{STRATEGY_VERSION_BASE}_room0_regime0_belock0"


def test_room_flag_moves_version():
    off = strategy_version({"entry_min_room_pips": 0.0})
    on = strategy_version({"entry_min_room_pips": 1.0})
    assert "room0" in off and "room1" in on and off != on


def test_regime_flag_moves_version():
    off = strategy_version({"entry_avoid_regimes": []})
    on = strategy_version({"entry_avoid_regimes": ["TREND_EXPANSION", "COMPRESSION"]})
    assert "regime0" in off and "regime1" in on


def test_belock_flag_moves_version():
    assert "belock1" in strategy_version({"be_lock_enabled": True})
    assert "belock0" in strategy_version({"be_lock_enabled": False})


def test_phases_are_distinct():
    phase1 = strategy_version({})
    phase2 = strategy_version({"entry_min_room_pips": 1.0})
    phase3 = strategy_version({"entry_min_room_pips": 1.0,
                               "entry_avoid_regimes": ["TREND_EXPANSION"]})
    assert len({phase1, phase2, phase3}) == 3


def test_trade_record_carries_version_field():
    # Field exists with a safe default so old records/callers are unaffected.
    r = TradeRecord(
        ticket=1, symbol="EURUSD", direction="BUY", entry_time="", entry_price=1.1,
        initial_sl=1.09, initial_tp=1.12, regime="", regime_confidence=0.0,
        mtf_alignment=0.0, mtf_context="", anomaly_velocity_z=0.0,
        displacement_classification="", nearest_support=0.0, nearest_resistance=0.0,
    )
    assert r.strategy_version == ""
    r.strategy_version = "entry_v5.1_room1_regime0_belock0"
    assert r.strategy_version.endswith("regime0_belock0")


def test_trade_record_carries_why_accepted_fields():
    # entry_state + setup_source default empty and are settable (populated in
    # record_entry from the snapshot). These are the group-by keys for
    # "which setup/state is profitable".
    r = TradeRecord(
        ticket=1, symbol="EURUSD", direction="BUY", entry_time="", entry_price=1.1,
        initial_sl=1.09, initial_tp=1.12, regime="RANGE_CHOP", regime_confidence=0.0,
        mtf_alignment=0.0, mtf_context="", anomaly_velocity_z=0.0,
        displacement_classification="", nearest_support=0.0, nearest_resistance=0.0,
    )
    assert r.entry_state == "" and r.setup_source == ""
    r.entry_state, r.setup_source = "RETEST_WAIT", "sweep"
    assert r.entry_state == "RETEST_WAIT" and r.setup_source == "sweep"


def test_entry_decision_has_confluence_score():
    # Raw gate score lives on EntryDecision, separate from signal_quality.
    from axonai.realtime.entry_state_machine import EntryDecision
    ed = EntryDecision()
    assert ed.confluence_score == 0.0
    ed.confluence_score = 0.55
    assert ed.confluence_score == 0.55


def test_trade_record_carries_confluence_score_and_serialises():
    # confluence_score is the number to correlate with pips_profit; it must be a
    # first-class field AND survive asdict() so it lands in trade_analytics.jsonl.
    from dataclasses import asdict
    r = TradeRecord(
        ticket=1, symbol="EURUSD", direction="BUY", entry_time="", entry_price=1.1,
        initial_sl=1.09, initial_tp=1.12, regime="", regime_confidence=0.0,
        mtf_alignment=0.0, mtf_context="", anomaly_velocity_z=0.0,
        displacement_classification="", nearest_support=0.0, nearest_resistance=0.0,
    )
    assert r.confluence_score == 0.0
    r.confluence_score = 0.62
    d = asdict(r)
    assert d["confluence_score"] == 0.62
    # Kept distinct from the inflated signal_quality so both are queryable.
    assert "signal_quality" in d and "confluence_score" in d
