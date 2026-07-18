"""Regression tests for the freeze-safe execution-safety hardening.

Covers the fixes in commits f086eb9 / 9094448 / d5c0d42:
  - RiskGuard daily-drawdown breaker arming
  - partial fills (DONE_PARTIAL) counted as success
  - direct-mode orders refused when they would route through the feed terminal
  - bridge orders refused instead of pricing off an EURUSD placeholder
  - the direct-mode position cap scoped by symbol
  - the execution client attaching the shared-secret token when configured
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import MetaTrader5 as mt5

from axonai.realtime.trade_executor import MT5TradeExecutor, TRADE_RETCODE_DONE_PARTIAL
from axonai.realtime.risk_guard import RiskGuard


def _sym_info():
    return MagicMock(visible=True, digits=5, point=0.00001,
                     trade_tick_value=1.0, trade_tick_size=0.00001,
                     volume_min=0.01, volume_step=0.01, volume_max=100.0)


class TestRiskGuardBreaker(unittest.TestCase):
    """The daily-drawdown breaker must arm once equity is fed (the bug: it never
    received equity in bridge mode, so is_tripped was always False)."""

    def _fresh_guard(self):
        rg = RiskGuard({"risk_max_daily_drawdown_pct": 5.0, "risk_max_daily_loss_amount": 500.0})
        # Isolate from the live reports/daily_pnl.json — never touch production state.
        tmp = tempfile.mkdtemp()
        rg.risk_pnl_log_file = os.path.join(tmp, "daily_pnl.json")
        rg.daily_pnl = {"date": str(date.today()), "start_equity": 0.0, "realized_pnl": 0.0}
        rg.current_equity = 0.0
        return rg

    def test_not_tripped_before_equity_seeded(self):
        rg = self._fresh_guard()
        self.assertFalse(rg.is_tripped)  # equity 0.0 must never trip

    def test_trips_on_drawdown_after_equity_fed(self):
        rg = self._fresh_guard()
        rg.update_equity(10000.0, 10000.0)   # seeds start_equity
        self.assertFalse(rg.is_tripped)      # no drawdown yet
        rg.current_equity = 9000.0           # 10% drawdown > 5% limit
        self.assertTrue(rg.is_tripped)


class TestExecutionSafety(unittest.TestCase):
    def setUp(self):
        self.config = {
            "realtime_magic_number": 999999,
            "realtime_default_lot_size": 0.02,
            "realtime_deviation": 10,
        }
        self.executor = MT5TradeExecutor(self.config)

    @patch("MetaTrader5.terminal_info")
    @patch("MetaTrader5.symbol_info")
    @patch("MetaTrader5.symbol_info_tick")
    @patch("MetaTrader5.positions_get")
    @patch("MetaTrader5.order_send")
    @patch("MetaTrader5.account_info")
    def test_partial_fill_counts_as_success(self, mock_acc, mock_send, mock_pos, mock_tick, mock_sym, mock_term):
        self.executor.config["allow_direct_feed_execution"] = True
        mock_term.return_value = True
        mock_acc.return_value = None
        mock_pos.return_value = ()
        mock_sym.return_value = _sym_info()
        mock_tick.return_value = MagicMock(ask=1.08500, bid=1.08480)
        mock_send.return_value = MagicMock(
            retcode=TRADE_RETCODE_DONE_PARTIAL, order=555, volume=0.01, price=1.08500,
            comment="partial", bid=1.08480, ask=1.08500, request_id=1,
        )
        res = self.executor.execute_signal("EURUSD", "Buy")
        self.assertIsNotNone(res)
        self.assertTrue(res["success"])   # a partial fill opened a real position
        self.assertEqual(res["order"], 555)

    @patch("MetaTrader5.account_info")
    @patch("MetaTrader5.terminal_info")
    def test_direct_live_order_blocked_without_trade_terminal(self, mock_term, mock_acc):
        mock_term.return_value = True
        mock_acc.return_value = None
        # No allow_direct_feed_execution; live order; get_mt5_trade() is None ->
        # the order would route through the feed/data terminal and must be refused.
        res = self.executor.execute_signal("EURUSD", "Buy")
        self.assertIsNotNone(res)
        self.assertEqual(res.get("reason"), "no_trade_terminal")

    @patch("axonai.realtime.execution_client.send_execution_command")
    def test_bridge_refuses_placeholder_pricing(self, mock_send):
        self.executor.config["realtime_execution_mode"] = "bridge"
        self.executor.config["realtime_dry_run"] = False
        self.executor.circuit_breaker = MagicMock(is_tripped=False)
        mock_send.return_value = {"success": True, "positions": [], "equity": 10000.0, "balance": 10000.0}
        live_state = MagicMock()
        live_state.current_bid = 0.0   # no tick yet -> must NOT price off a placeholder
        live_state.current_ask = 0.0
        res = self.executor.execute_signal("XAUUSD", "Buy", live_state=live_state)
        self.assertIsNotNone(res)
        self.assertEqual(res.get("reason"), "no_live_price")
        actions = [c.args[1].get("action") for c in mock_send.call_args_list]
        self.assertNotIn("open", actions)   # never sent an order

    @patch("MetaTrader5.terminal_info")
    @patch("MetaTrader5.symbol_info")
    @patch("MetaTrader5.symbol_info_tick")
    @patch("MetaTrader5.positions_get")
    @patch("MetaTrader5.order_send")
    @patch("MetaTrader5.account_info")
    def test_direct_position_cap_scoped_by_symbol(self, mock_acc, mock_send, mock_pos, mock_tick, mock_sym, mock_term):
        self.executor.config["allow_direct_feed_execution"] = True
        mock_term.return_value = True
        mock_acc.return_value = None
        mock_pos.return_value = ()
        mock_sym.return_value = _sym_info()
        mock_tick.return_value = MagicMock(ask=1.08500, bid=1.08480)
        mock_send.return_value = MagicMock(
            retcode=mt5.TRADE_RETCODE_DONE, order=1, volume=0.02, price=1.08500,
            comment="ok", bid=1.08480, ask=1.08500, request_id=1,
        )
        self.executor.execute_signal("EURUSD", "Buy")
        # The per-magic conflict cap must be scoped to the symbol, not portfolio-wide.
        mock_pos.assert_any_call(symbol="EURUSD")


class TestBridgeTokenAuth(unittest.TestCase):
    def test_client_attaches_token_when_configured(self):
        from axonai.realtime import execution_client
        with patch.object(execution_client, "run_coroutine", return_value={"success": True}), \
             patch.object(execution_client, "_ws_send_cmd", new_callable=MagicMock) as mock_ws:
            execution_client.send_execution_command(
                {"realtime_execution_bridge_token": "s3cret"}, {"action": "ping"}
            )
            mock_ws.assert_called_once()
            sent_req = mock_ws.call_args[0][1]
            self.assertEqual(sent_req.get("token"), "s3cret")

    def test_client_no_token_when_unset(self):
        from axonai.realtime import execution_client
        os.environ.pop("AXON_BRIDGE_TOKEN", None)
        with patch.object(execution_client, "run_coroutine", return_value={"success": True}), \
             patch.object(execution_client, "_ws_send_cmd", new_callable=MagicMock) as mock_ws:
            execution_client.send_execution_command({}, {"action": "ping"})
            sent_req = mock_ws.call_args[0][1]
            self.assertNotIn("token", sent_req)


if __name__ == "__main__":
    unittest.main()
