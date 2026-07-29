"""Tests for dual-terminal isolation: per-instance files + a thin execution node.

The lead (Eightcap) and the execution node (FundingPips) run as two OS processes
from the SAME working directory — the MetaTrader5 binding is a per-process
singleton, so a second account cannot be driven from one process. That makes two
things load-bearing, and both are covered here:

  1. FILE ISOLATION. Every per-instance report path must carry the node's tag.
     Untagged, the two processes append two different accounts' P&L into one
     stream (and two RotatingFileHandlers fight over one daemon.log, whose
     Windows rollover then fails on a sharing violation).

  2. A THIN NODE. The node never acts on its own detected signals — the daemon's
     event loop discards every event it emits — so detection is pure waste and
     its logs are actively misleading. But ``live_state`` must STILL be fed:
     ``inject_signal`` reads ``atr_14_h1`` from it to size the stop of every
     order routed in from the lead. Skipping too much would silently fall back
     to the executor's 0.15%-of-price default stop.

No MT5 and no network: fakes throughout.
"""

from __future__ import annotations

import types
import unittest
from datetime import datetime, timedelta, timezone

from axonai.realtime.daemon import AxonDaemon
from axonai.realtime.event_detector import EventDetector
from axonai.realtime.exec_node import ExecNodeServer
from axonai.realtime.news_guard import NewsGuard, _FAILURE_RETRY


# ── 1. per-instance report paths ──────────────────────────────────────────────

class TestReportPathTagging(unittest.TestCase):
    def _daemon(self, tag):
        d = AxonDaemon.__new__(AxonDaemon)
        d.config = {"instance_tag": tag}
        return d

    def test_lead_keeps_historical_untagged_names(self):
        d = self._daemon("")
        # The live account's dashboard reads these; renaming them would orphan
        # its existing history.
        self.assertTrue(d._report_path("signals.jsonl").endswith("signals.jsonl"))
        self.assertNotIn("_node", d._report_path("signals.jsonl"))

    def test_node_paths_are_tagged_before_the_extension(self):
        d = self._daemon("_node")
        self.assertTrue(d._report_path("signals.jsonl").endswith("signals_node.jsonl"))
        self.assertTrue(d._report_path("signals.log").endswith("signals_node.log"))
        self.assertTrue(
            d._report_path("dry_run_session.jsonl").endswith("dry_run_session_node.jsonl"))

    def test_lead_and_node_never_collide(self):
        lead, node = self._daemon(""), self._daemon("_node")
        for name in ("signals.jsonl", "signals.log", "dry_run_session.jsonl"):
            self.assertNotEqual(lead._report_path(name), node._report_path(name))

    def test_missing_tag_key_degrades_to_lead_naming(self):
        d = AxonDaemon.__new__(AxonDaemon)
        d.config = {}          # e.g. a daemon constructed straight from DEFAULT_CONFIG
        self.assertTrue(d._report_path("signals.log").endswith("signals.log"))


# ── 2. the node is thin, but live_state stays warm ───────────────────────────

class _SpyLiveState:
    """Records calls and exposes the one field the node genuinely needs."""

    def __init__(self):
        self.symbol = "EURUSD"
        self.is_initialized = True
        self.tick_calls = 0
        self.candle_calls = 0
        self._state = types.SimpleNamespace(
            atr_14_h1=0.0012, session="london", session_penalty=0.0,
            spread_pips=0.8, spread_safe=True,
        )

    def on_tick(self, bid, ask, ts):
        self.tick_calls += 1
        return False

    def on_candle_close(self, candle):
        self.candle_calls += 1


class _SpyEvidence:
    def __init__(self):
        self.tick_calls = 0
        self.candle_calls = 0
        self.price_levels = []
        self._m15_candles = []
        self._level_tracker = types.SimpleNamespace(update=lambda *a, **k: None)

    def on_tick(self, bid, ask, ts):
        self.tick_calls += 1

    def on_candle_close(self, candle):
        self.candle_calls += 1

    def snapshot(self):
        return types.SimpleNamespace(trend_direction_h4="up", trend_direction_h1="up")


def _detector(exec_node):
    d = EventDetector.__new__(EventDetector)
    d.config = {}
    d.live_state = _SpyLiveState()
    d.live_evidence = _SpyEvidence()
    d.exec_node = exec_node
    d.event_queue = None                    # any _emit would blow up → proves none happen
    d._previous_session = "london"
    d._previous_spread_safe = True
    d._pip_mult = 0.0001
    d._log_events = True
    d._current_trigger_candle = None
    d._structure_detected_on_candle = False
    d.peak_detector_opt = types.SimpleNamespace(
        update=_CountingUpdate(), pip_mult=0.0001)
    d.peak_detector = d.peak_detector_opt
    return d


class _CountingUpdate:
    def __init__(self):
        self.calls = 0

    def __call__(self, mid, ts):
        self.calls += 1
        return None


class TestExecNodeDetectorIsThin(unittest.TestCase):
    def test_node_tick_updates_live_state_only(self):
        d = _detector(exec_node=True)
        for i in range(5):
            d.on_tick(1.1000, 1.1001, datetime.now(timezone.utc))
        # atr_14_h1 feeds every routed order's stop → must stay warm.
        self.assertEqual(d.live_state.tick_calls, 5)
        # ...and nothing else runs: no evidence rebuild, no peak detector.
        self.assertEqual(d.live_evidence.tick_calls, 0)
        self.assertEqual(d.peak_detector_opt.update.calls, 0)

    def test_lead_tick_runs_the_full_stack(self):
        d = _detector(exec_node=False)
        d.on_tick(1.1000, 1.1001, datetime.now(timezone.utc))
        self.assertEqual(d.live_state.tick_calls, 1)
        self.assertEqual(d.live_evidence.tick_calls, 1)     # the expensive rebuild
        self.assertEqual(d.peak_detector_opt.update.calls, 1)

    def test_node_candle_close_keeps_atr_but_skips_structure(self):
        d = _detector(exec_node=True)
        candle = types.SimpleNamespace(
            timeframe="M15", open=1.1, high=1.11, low=1.09, close=1.105,
            volume=10, open_time=datetime.now(timezone.utc))
        d.on_candle_close(candle)
        self.assertEqual(d.live_state.candle_calls, 1)      # ATR source
        self.assertEqual(d.live_evidence.candle_calls, 0)   # structural work skipped

    def test_atr_is_still_readable_by_inject_signal_on_a_node(self):
        """The whole point of keeping live_state: a real ATR, not the fallback."""
        d = _detector(exec_node=True)
        d.on_tick(1.1000, 1.1001, datetime.now(timezone.utc))
        self.assertGreater(d.live_state._state.atr_14_h1, 0.0)


class TestExecNodeStatsLine(unittest.TestCase):
    def _shell(self, exec_node):
        d = AxonDaemon.__new__(AxonDaemon)
        d._exec_node = exec_node
        d._start_time = datetime.now() - timedelta(minutes=3)
        d.mt5_symbol = "EURUSD"
        d.config = {}
        d.tick_engine = types.SimpleNamespace(_tick_count=42)
        d._tracked_positions = set()
        d._orders_routed = 7
        d._events_detected = 99
        d._events_fired = 0
        d._events_skipped = 0
        d.event_detector = types.SimpleNamespace()
        return d

    def test_node_reports_orders_routed_not_events(self):
        d = self._shell(exec_node=True)
        with self.assertLogs("axonai.realtime.daemon", level="INFO") as cm:
            d._log_stats()
        line = "\n".join(cm.output)
        self.assertIn("EXEC-NODE", line)
        self.assertIn("orders_routed=7", line)
        # "events_fired=0 | events_skipped=0" forever is the misleading noise.
        self.assertNotIn("events_fired", line)

    def test_lead_still_reports_the_detection_stats(self):
        d = self._shell(exec_node=False)
        with self.assertLogs("axonai.realtime.daemon", level="INFO") as cm:
            d._log_stats()
        line = "\n".join(cm.output)
        self.assertIn("STATS:", line)
        self.assertIn("events_detected=99", line)

    def test_routed_counter_survives_a_daemon_built_without_init(self):
        d = AxonDaemon.__new__(AxonDaemon)
        d.mt5_symbol = "EURUSD"
        d.config = {}
        d.trade_executor = types.SimpleNamespace(
            execute_signal=lambda *a, **k: None)
        d.live_state = _SpyLiveState()
        d.correlation_engine = None
        d._tracked_positions = set()
        # No _orders_routed attribute at all: a telemetry counter must never be
        # what stops a routed order from reaching the broker.
        self.assertIsNone(d.inject_signal("Buy"))
        self.assertEqual(d._orders_routed, 1)


# ── 3. size_scale must not silently invert ───────────────────────────────────

class _FakeNodeDaemon:
    def __init__(self):
        self.calls = []

    def inject_signal(self, signal, size_scale, source="mirror"):
        self.calls.append((signal, size_scale))
        return {"order": 1, "volume": 1.0}

    def inject_close(self, reason):
        return 0


class TestSizeScaleGuard(unittest.TestCase):
    def _dispatch(self, payload):
        fake = _FakeNodeDaemon()
        ack = ExecNodeServer({"EURUSD": fake}, port=0)._dispatch(payload)
        return ack, fake

    def test_zero_scale_declines_instead_of_going_full_size(self):
        # `float(x or 1.0)` treats 0.0 as falsy: a 0.0 scale used to become 1.0,
        # opening a FULL-size position — the exact opposite of the instruction.
        ack, fake = self._dispatch(
            {"cmd": "enter", "symbol": "EURUSD", "signal": "Buy", "size_scale": 0.0})
        self.assertFalse(ack["ok"])
        self.assertEqual(fake.calls, [])

    def test_negative_scale_declines(self):
        ack, fake = self._dispatch(
            {"cmd": "enter", "symbol": "EURUSD", "signal": "Buy", "size_scale": -1.0})
        self.assertFalse(ack["ok"])
        self.assertEqual(fake.calls, [])

    def test_missing_scale_still_defaults_to_full_size(self):
        ack, fake = self._dispatch({"cmd": "enter", "symbol": "EURUSD", "signal": "Buy"})
        self.assertTrue(ack["ok"])
        self.assertEqual(fake.calls, [("Buy", 1.0)])

    def test_garbage_scale_defaults_to_full_size(self):
        ack, fake = self._dispatch(
            {"cmd": "enter", "symbol": "EURUSD", "signal": "Buy", "size_scale": "abc"})
        self.assertTrue(ack["ok"])
        self.assertEqual(fake.calls, [("Buy", 1.0)])

    def test_partial_scale_is_forwarded_untouched(self):
        ack, fake = self._dispatch(
            {"cmd": "enter", "symbol": "EURUSD", "signal": "Buy", "size_scale": 0.5})
        self.assertTrue(ack["ok"])
        self.assertEqual(fake.calls, [("Buy", 0.5)])


# ── 4. news calendar: back off on failure ────────────────────────────────────

class TestNewsGuardFailureBackoff(unittest.TestCase):
    def _guard(self):
        g = NewsGuard({"news_guard_enabled": True})
        g.enabled = True
        return g

    def test_failed_refresh_does_not_refetch_on_every_candle_close(self):
        g = self._guard()
        attempts = []
        g._fetch_remote = lambda: (attempts.append(1), None)[1]
        g._load_cache = lambda: None

        now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        g.refresh(now_utc=now)
        self.assertEqual(len(attempts), 1)

        # Four more candle closes inside the backoff window. Previously each one
        # re-ran a blocking 15s HTTP fetch and emitted two warnings, because the
        # staleness guard also requires self._events — empty exactly when down.
        for i in range(1, 5):
            g.refresh(now_utc=now + timedelta(minutes=i))
        self.assertEqual(len(attempts), 1)

        # Once the backoff expires it does try again.
        g.refresh(now_utc=now + _FAILURE_RETRY + timedelta(minutes=1))
        self.assertEqual(len(attempts), 2)

    def test_force_still_overrides_the_backoff(self):
        g = self._guard()
        attempts = []
        g._fetch_remote = lambda: (attempts.append(1), None)[1]
        g._load_cache = lambda: None
        now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        g.refresh(now_utc=now)
        g.refresh(now_utc=now, force=True)
        self.assertEqual(len(attempts), 2)

    def test_success_clears_the_backoff(self):
        g = self._guard()
        g._fetch_remote = lambda: []
        g._save_cache = lambda raw: None
        now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        g.refresh(now_utc=now)
        self.assertIsNone(g._retry_after)


if __name__ == "__main__":
    unittest.main()
