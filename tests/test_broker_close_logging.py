"""Broker-side closes (TP / SL / stop-out) must reach trade_analytics.jsonl.

Before this, record_exit was only ever called from the daemon's own close paths,
so a position that ran to its take-profit vanished from the log. That made the log
a censored sample: it contained engine-cut trades only, biased against every trade
held long enough to reach a barrier.
"""
import json
import os
import types

from axonai.realtime.trade_analytics import TradeAnalytics, _classify_gate


def _snap(mfe=0.0):
    vel = types.SimpleNamespace(
        vol_pips=0.9, percentile=70.0, tick_efficiency=0.5,
        decay_ratio=0.3, is_unusual=False, z_score=0.0,
    )
    health = types.SimpleNamespace(
        max_favorable_excursion=mfe, max_adverse_excursion=0.4,
        time_in_drawdown_sec=1.0, score=1.0,
    )
    return types.SimpleNamespace(
        velocity=vel, displacement=None, mtf=None, regime=None,
        liquidity=None, location_context=None, entry_decision=None,
        trade_health=health, trade_state=None,
    )


def _read_last(path):
    with open(path, encoding="utf-8") as f:
        return json.loads([ln for ln in f if ln.strip()][-1])


def _rows(path):
    if not os.path.exists(path):   # nothing written at all
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def test_tp_close_is_recorded_with_money_and_source(tmp_path):
    ta = TradeAnalytics(log_dir=str(tmp_path))
    ta.record_entry(1, "USDJPY", "BUY", 163.672, 163.552, 163.885, _snap())
    ta.record_exit(
        1, 163.885, 21.3, "Take Profit (TP) Hit", _snap(mfe=21.3),
        exit_time="2026-07-23T19:17:31", profit_usd=197.55, volume=1.52,
        exit_source="broker",
    )
    rec = _read_last(ta._log_file)
    assert rec["exit_gate"] == "take_profit"
    assert rec["exit_source"] == "broker"
    assert rec["profit_usd"] == 197.55
    assert rec["volume"] == 1.52
    assert rec["exit_time"] == "2026-07-23T19:17:31"   # real deal time, not now()
    assert rec["pips_profit"] == 21.3


def test_sl_and_stopout_reasons_classify(tmp_path):
    assert _classify_gate("Stop Loss (SL) Hit") == "hard_sl"
    assert _classify_gate("Trailing SL Hit") == "trailing"
    assert _classify_gate("Take Profit (TP) Hit") == "take_profit"


def test_engine_close_then_broker_detection_writes_once(tmp_path):
    """The daemon closes, records, and only later notices the ticket vanished.

    That second call must not append a duplicate row.
    """
    ta = TradeAnalytics(log_dir=str(tmp_path))
    ta.record_entry(2, "EURUSD", "SELL", 1.14042, 1.14162, 1.13883, _snap())
    ta.record_exit(2, 1.13920, 12.2, "Thesis failure: displacement reversed", _snap(mfe=15.0))
    ta.record_exit(  # broker-close detector sees the same ticket gone
        2, 1.13920, 12.2, "Manual Close / Unknown", _snap(),
        profit_usd=145.18, exit_source="broker",
    )
    rows = _rows(ta._log_file)
    assert len(rows) == 1
    assert rows[0]["exit_source"] == "engine"          # first writer wins
    assert rows[0]["exit_gate"] == "thesis_failure"


def test_unknown_ticket_is_ignored(tmp_path):
    """Adopted / manually-opened positions have no entry record; skip them."""
    ta = TradeAnalytics(log_dir=str(tmp_path))
    ta.record_exit(999, 1.1, 3.0, "Take Profit (TP) Hit", _snap(), exit_source="broker")
    assert _rows(ta._log_file) == []


def test_defaults_when_broker_fields_absent(tmp_path):
    ta = TradeAnalytics(log_dir=str(tmp_path))
    ta.record_entry(3, "EURUSD", "BUY", 1.10000, 1.09920, 1.10160, _snap())
    ta.record_exit(3, 1.10050, 5.0, "Thesis failure", _snap())
    rec = _read_last(ta._log_file)
    assert rec["exit_source"] == "engine"
    assert rec["profit_usd"] == 0.0
    assert rec["volume"] == 0.0
    assert rec["exit_time"]            # falls back to now()
