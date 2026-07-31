"""Unit tests for the config-gated directional BUY-side skips (_buy_skip_reason).

Validated OOS (2026-06 & 2026-07 both net-negative for BUYs): panic-regime and
active-session (08-16 UTC) BUYs are the worst pockets; all-BUY is the full bet.
All gates default OFF and must never touch SELLs.

The window is bucketed on TRUE UTC. Hour 07 must stay tradeable — it is
net-positive in both months, and the superseded 07-12 "London" window (built on
a 3-hour-skewed clock) wrongly suppressed it.
"""
import unittest
from datetime import datetime, timezone

from axonai.realtime.daemon import AxonDaemon


def utc(h):
    return datetime(2026, 8, 3, h, 30, tzinfo=timezone.utc)


class TestBuySkips(unittest.TestCase):
    def r(self, signal, regime, hour, **gates):
        cfg = {"entry_skip_panic_buy": False, "entry_skip_session_buy": False,
               "entry_skip_all_buy": False}
        cfg.update(gates)
        return AxonDaemon._buy_skip_reason(signal, regime, utc(hour), cfg)

    # defaults off -> never skip
    def test_all_gates_off_never_skips(self):
        self.assertIsNone(self.r("Buy", "panic", 9))
        self.assertIsNone(self.r("Buy", "ranging", 9))

    # SELLs are never touched, whatever the gates
    def test_sells_never_skipped(self):
        self.assertIsNone(self.r("Sell", "panic", 9,
                                 entry_skip_panic_buy=True, entry_skip_session_buy=True,
                                 entry_skip_all_buy=True))

    # panic gate
    def test_panic_buy_skipped_only_in_panic(self):
        self.assertEqual(self.r("Buy", "panic", 3, entry_skip_panic_buy=True), "panic-regime BUY")
        self.assertIsNone(self.r("Buy", "ranging", 3, entry_skip_panic_buy=True))

    def test_panic_gate_case_insensitive(self):
        self.assertEqual(self.r("Buy", "PANIC", 3, entry_skip_panic_buy=True), "panic-regime BUY")

    # session gate: 08-16 UTC, start-inclusive / end-exclusive
    def test_session_buy_window(self):
        want = "active-session BUY (08-16 UTC)"
        self.assertEqual(self.r("Buy", "ranging", 8, entry_skip_session_buy=True), want)
        self.assertEqual(self.r("Buy", "ranging", 15, entry_skip_session_buy=True), want)
        self.assertIsNone(self.r("Buy", "ranging", 16, entry_skip_session_buy=True))  # end-exclusive

    def test_hour_07_stays_tradeable(self):
        """Regression: the old 07-12 window suppressed hour 07, which is
        net-positive in both validation months (+28 June / +29 July)."""
        self.assertIsNone(self.r("Buy", "ranging", 7, entry_skip_session_buy=True))

    def test_session_window_is_configurable(self):
        self.assertEqual(
            self.r("Buy", "ranging", 20, entry_skip_session_buy=True,
                   entry_skip_session_buy_start=19, entry_skip_session_buy_end=22),
            "active-session BUY (19-22 UTC)")
        self.assertIsNone(self.r("Buy", "ranging", 9, entry_skip_session_buy=True,
                                 entry_skip_session_buy_start=19,
                                 entry_skip_session_buy_end=22))

    # all-buy suppression wins regardless of regime/hour
    def test_all_buy_suppresses_everything(self):
        self.assertEqual(self.r("Buy", "ranging", 18, entry_skip_all_buy=True), "all-BUY suppression")

    # combined B config (panic + session) leaves a calm off-session BUY alone
    def test_B_config_allows_calm_offsession_buy(self):
        self.assertIsNone(self.r("Buy", "ranging", 18,
                                 entry_skip_panic_buy=True, entry_skip_session_buy=True))


if __name__ == "__main__":
    unittest.main()
