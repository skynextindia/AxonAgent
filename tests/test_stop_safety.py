"""Stop-safety regression tests (no live MT5).

Covers the two wave-introduced defects fixed today:
  * daemon._modify_sl — a rejected trailing-SL modify must be logged AND alerted
    (throttled), never silently swallowed; a success clears the throttle.
  * DaemonSupervisor._check_thread_liveness — a dead pair thread must raise a
    loud alert exactly once, and flatten only when opted in.
"""

from __future__ import annotations

import threading
import types
import unittest

import axonai.realtime.daemon as daemon_mod
import axonai.realtime.supervisor as sup_mod
from axonai.realtime.daemon import AxonDaemon
from axonai.realtime.supervisor import DaemonSupervisor


class _FakeMt5:
    TRADE_ACTION_SLTP = 1
    TRADE_RETCODE_DONE = 10009

    def __init__(self, retcode):
        self._retcode = retcode
        self.sent = []

    def order_send(self, request):
        self.sent.append(request)
        if self._retcode is None:
            return None
        return types.SimpleNamespace(retcode=self._retcode, comment="test")


def _daemon_shell():
    d = AxonDaemon.__new__(AxonDaemon)
    d.mt5_symbol = "EURUSD.i"
    d.config = {}
    d._sl_fail_alert_ts = {}
    return d


class TestModifySl(unittest.TestCase):
    def setUp(self):
        self._orig_mt5 = daemon_mod.mt5
        self._orig_alert = daemon_mod.send_alert
        self.alerts = []
        daemon_mod.send_alert = lambda msg, cfg: self.alerts.append(msg)

    def tearDown(self):
        daemon_mod.mt5 = self._orig_mt5
        daemon_mod.send_alert = self._orig_alert

    def test_success_returns_true_and_no_alert(self):
        daemon_mod.mt5 = _FakeMt5(_FakeMt5.TRADE_RETCODE_DONE)
        d = _daemon_shell()
        self.assertTrue(d._modify_sl(111, 1.1000, 1.1100, "BUY"))
        self.assertEqual(self.alerts, [])

    def test_failure_returns_false_and_alerts(self):
        daemon_mod.mt5 = _FakeMt5(10004)   # requote-style reject
        d = _daemon_shell()
        self.assertFalse(d._modify_sl(222, 1.2000, 1.2100, "SELL"))
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("FAILED", self.alerts[0])

    def test_none_result_is_a_failure(self):
        daemon_mod.mt5 = _FakeMt5(None)
        d = _daemon_shell()
        self.assertFalse(d._modify_sl(333, 1.3000, 1.3100, "BUY"))
        self.assertEqual(len(self.alerts), 1)

    def test_alert_is_throttled_per_ticket(self):
        daemon_mod.mt5 = _FakeMt5(10004)
        d = _daemon_shell()
        d._modify_sl(444, 1.1000, 1.1100, "BUY")
        d._modify_sl(444, 1.1000, 1.1100, "BUY")   # same ticket, within 60s
        self.assertEqual(len(self.alerts), 1)       # only one alert

    def test_success_clears_throttle(self):
        d = _daemon_shell()
        daemon_mod.mt5 = _FakeMt5(10004)
        d._modify_sl(555, 1.1000, 1.1100, "BUY")    # fail -> throttle set
        self.assertIn(555, d._sl_fail_alert_ts)
        daemon_mod.mt5 = _FakeMt5(_FakeMt5.TRADE_RETCODE_DONE)
        d._modify_sl(555, 1.1000, 1.1100, "BUY")    # success -> throttle cleared
        self.assertNotIn(555, d._sl_fail_alert_ts)


class _FakeThread:
    def __init__(self, alive):
        self._alive = alive

    def is_alive(self):
        return self._alive


class _FakeDaemon:
    def __init__(self):
        self.closed_with = None

    def _close_all_positions(self, reason):
        self.closed_with = reason
        return 2


def _sup_shell(config=None, thread_alive=False, daemon=None):
    s = DaemonSupervisor.__new__(DaemonSupervisor)
    s.base_config = config or {}
    s._stopping = threading.Event()
    s._dead_alerted = set()
    s._thread_by_symbol = {"EURUSD": _FakeThread(thread_alive)}
    s.daemons = {"EURUSD": daemon} if daemon else {}
    return s


class TestSupervisorWatchdog(unittest.TestCase):
    def setUp(self):
        self._orig_alert = sup_mod.send_alert
        self.alerts = []
        sup_mod.send_alert = lambda msg, cfg: self.alerts.append(msg)

    def tearDown(self):
        sup_mod.send_alert = self._orig_alert

    def test_alive_thread_no_action(self):
        s = _sup_shell(thread_alive=True)
        s._check_thread_liveness()
        self.assertEqual(self.alerts, [])
        self.assertEqual(s._dead_alerted, set())

    def test_dead_thread_alerts_once(self):
        s = _sup_shell(thread_alive=False)
        s._check_thread_liveness()
        s._check_thread_liveness()   # second pass must NOT re-alert
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("EURUSD", s._dead_alerted)

    def test_stopping_suppresses_watchdog(self):
        s = _sup_shell(thread_alive=False)
        s._stopping.set()
        s._check_thread_liveness()
        self.assertEqual(self.alerts, [])

    def test_flatten_off_by_default(self):
        d = _FakeDaemon()
        s = _sup_shell(thread_alive=False, daemon=d)
        s._check_thread_liveness()
        self.assertIsNone(d.closed_with)          # not flattened

    def test_flatten_opt_in(self):
        d = _FakeDaemon()
        s = _sup_shell(config={"supervisor_flatten_on_thread_death": True},
                       thread_alive=False, daemon=d)
        s._check_thread_liveness()
        self.assertEqual(d.closed_with, "supervisor: daemon thread died")


if __name__ == "__main__":
    unittest.main()
