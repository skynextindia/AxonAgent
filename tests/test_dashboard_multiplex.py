"""Tests for the multi-pair dashboard multiplex (Phase 3 / F1).

Exercises the DashboardServer registry + per-symbol history routing without
starting the HTTP server or any WebSocket connections (broadcast returns after
updating the cache when there are no connections). Runs in a temp cwd so the
session file is never touched.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from axonai.realtime.api_server import DashboardServer


class _Obj:
    pass


class TestDashboardMultiplex(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.mkdtemp()
        os.chdir(self._tmp)
        self.s = DashboardServer(host="127.0.0.1", port=0)
        self.s._save_session = lambda: None  # no disk writes during tests

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_canon_normalizes(self):
        self.assertEqual(self.s._canon("EURUSDm"), "EURUSD")
        self.assertEqual(self.s._canon("USDJPY.i"), "USDJPY")
        self.assertEqual(self.s._canon("eurusd=x"), "EURUSD")

    def test_register_first_is_active(self):
        d = _Obj()
        self.s.register_daemon("EURUSD", d)
        self.assertEqual(self.s.active_symbol, "EURUSD")
        self.assertIs(self.s.daemon, d)
        # A second registration does not steal active.
        self.s.register_daemon("USDJPY", _Obj())
        self.assertEqual(self.s.active_symbol, "EURUSD")

    def test_broadcast_routes_by_symbol(self):
        self.s.register_daemon("EURUSD", _Obj())
        self.s.register_daemon("USDJPY", _Obj())
        self.s.broadcast({"type": "event", "symbol": "USDJPY", "id": 1})
        self.s.broadcast({"type": "event", "symbol": "EURUSD", "id": 2})
        jpy = [e["id"] for e in self.s._history_for("USDJPY")["events"]]
        eur = [e["id"] for e in self.s._history_for("EURUSD")["events"]]
        self.assertEqual(jpy, [1])
        self.assertEqual(eur, [2])

    def test_history_property_follows_active(self):
        self.s.register_daemon("EURUSD", _Obj())
        self.s.register_daemon("USDJPY", _Obj())
        self.s.broadcast({"type": "levels", "symbol": "USDJPY", "v": 1})
        # Active is EURUSD → its history has no USDJPY levels.
        self.assertIsNone(self.s.history["levels"])
        self.s.active_symbol = "USDJPY"
        self.assertEqual(self.s.history["levels"]["v"], 1)

    def test_untagged_broadcast_goes_to_active(self):
        self.s.register_daemon("EURUSD", _Obj())
        self.s.broadcast({"type": "regime", "conf": 0.9})  # no symbol tag
        self.assertEqual(self.s.history["regime"]["conf"], 0.9)

    # ── live-stream filtering (multi-pair display isolation) ──────────────────
    def test_stream_filters_background_pair_in_multi(self):
        self.s.register_daemon("EURUSD", _Obj())
        self.s.register_daemon("USDJPY", _Obj())  # active stays EURUSD
        # Active pair streams; the background pair is dropped from the live feed.
        self.assertTrue(self.s._should_stream({"type": "tick", "symbol": "EURUSD"}))
        self.assertFalse(self.s._should_stream({"type": "tick", "symbol": "USDJPY"}))
        # Broker-suffixed tags canonicalize before the comparison.
        self.assertTrue(self.s._should_stream({"type": "tick", "symbol": "EURUSDm"}))
        self.assertFalse(self.s._should_stream({"type": "tick", "symbol": "USDJPY.i"}))

    def test_stream_passes_symbol_agnostic_in_multi(self):
        self.s.register_daemon("EURUSD", _Obj())
        self.s.register_daemon("USDJPY", _Obj())
        # Market-wide messages (no symbol) always reach every client.
        self.assertTrue(self.s._should_stream({"type": "news_data"}))

    def test_stream_follows_active_after_switch(self):
        self.s.register_daemon("EURUSD", _Obj())
        self.s.register_daemon("USDJPY", _Obj())
        self.s.active_symbol = "USDJPY"  # user clicked USD/JPY
        self.assertTrue(self.s._should_stream({"type": "tick", "symbol": "USDJPY"}))
        self.assertFalse(self.s._should_stream({"type": "tick", "symbol": "EURUSD"}))

    def test_stream_unfiltered_in_single_pair(self):
        self.s.register_daemon("EURUSD", _Obj())  # only one daemon → no filtering
        # A non-active symbol still streams so single-pair (any symbol) follows it.
        self.assertTrue(self.s._should_stream({"type": "tick", "symbol": "USDJPY"}))


if __name__ == "__main__":
    unittest.main()
