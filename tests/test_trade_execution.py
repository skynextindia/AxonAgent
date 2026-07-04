"""Unit and integration tests for MT5TradeExecutor."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from axonai.realtime.trade_executor import MT5TradeExecutor


class TestMT5TradeExecutor(unittest.TestCase):
    """Test suite for MT5TradeExecutor with mocked MT5 interface."""

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
    @patch("MetaTrader5.order_send")
    @patch("MetaTrader5.account_info")
    def test_execute_signal_buy(self, mock_acc_info, mock_order_send, mock_tick, mock_sym_info, mock_term_info):
        """Test BUY signal order composition and execution."""
        import MetaTrader5 as mt5

        # Mock MT5 return values
        mock_term_info.return_value = True
        mock_acc_info.return_value = None

        mock_symbol_info = MagicMock()
        mock_symbol_info.visible = True
        mock_sym_info.return_value = mock_symbol_info

        mock_tick_info = MagicMock()
        mock_tick_info.ask = 1.08500
        mock_tick_info.bid = 1.08480
        mock_tick.return_value = mock_tick_info

        mock_result = MagicMock()
        mock_result.retcode = mt5.TRADE_RETCODE_DONE
        mock_result.order = 12345
        mock_result.volume = 0.02
        mock_result.price = 1.08500
        mock_result.comment = "Success"
        mock_order_send.return_value = mock_result

        # Execute
        res = self.executor.execute_signal("EURUSDm", "Buy")

        # Verify
        self.assertIsNotNone(res)
        self.assertEqual(res["retcode"], mt5.TRADE_RETCODE_DONE)
        self.assertEqual(res["order"], 12345)
        self.assertEqual(res["volume"], 0.02)
        self.assertEqual(res["price"], 1.08500)

        # Verify order_send arguments
        mock_order_send.assert_called_once()
        sent_request = mock_order_send.call_args[0][0]
        self.assertEqual(sent_request["symbol"], "EURUSDm")
        self.assertEqual(sent_request["volume"], 0.02)
        self.assertEqual(sent_request["magic"], 999999)
        self.assertEqual(sent_request["deviation"], 10)
        self.assertEqual(sent_request["type"], mt5.ORDER_TYPE_BUY)

    @patch("MetaTrader5.terminal_info")
    @patch("MetaTrader5.symbol_info")
    @patch("MetaTrader5.symbol_info_tick")
    @patch("MetaTrader5.order_send")
    @patch("MetaTrader5.account_info")
    def test_execute_signal_sell(self, mock_acc_info, mock_order_send, mock_tick, mock_sym_info, mock_term_info):
        """Test SELL signal order composition and execution."""
        import MetaTrader5 as mt5

        # Mock MT5 return values
        mock_term_info.return_value = True
        mock_acc_info.return_value = None

        mock_symbol_info = MagicMock()
        mock_symbol_info.visible = True
        mock_sym_info.return_value = mock_symbol_info

        mock_tick_info = MagicMock()
        mock_tick_info.ask = 1.08500
        mock_tick_info.bid = 1.08480
        mock_tick.return_value = mock_tick_info

        mock_result = MagicMock()
        mock_result.retcode = mt5.TRADE_RETCODE_DONE
        mock_result.order = 12346
        mock_result.volume = 0.02
        mock_result.price = 1.08480
        mock_result.comment = "Success"
        mock_order_send.return_value = mock_result

        # Execute
        res = self.executor.execute_signal("EURUSDm", "Sell")

        # Verify
        self.assertIsNotNone(res)
        self.assertEqual(res["retcode"], mt5.TRADE_RETCODE_DONE)
        self.assertEqual(res["order"], 12346)
        self.assertEqual(res["price"], 1.08480)

        # Verify order_send arguments
        mock_order_send.assert_called_once()
        sent_request = mock_order_send.call_args[0][0]
        self.assertEqual(sent_request["type"], mt5.ORDER_TYPE_SELL)

    def test_execute_signal_hold(self):
        """Test that HOLD signals return None and make no calls."""
        res = self.executor.execute_signal("EURUSDm", "Hold")
        self.assertIsNone(res)

    @patch("MetaTrader5.terminal_info")
    @patch("MetaTrader5.symbol_info")
    @patch("MetaTrader5.symbol_info_tick")
    @patch("MetaTrader5.order_send")
    @patch("MetaTrader5.account_info")
    def test_execute_signal_dry_run_fixed_lot(self, mock_acc_info, mock_order_send, mock_tick, mock_sym_info, mock_term_info):
        """Test that dryrun config overrides lot size to 1.00 and returns sl."""
        import MetaTrader5 as mt5

        # Enable dry run
        self.executor.config["realtime_dry_run"] = True

        mock_term_info.return_value = True
        mock_acc_info.return_value = MagicMock(equity=50000.0, balance=50000.0)

        mock_symbol_info = MagicMock()
        mock_symbol_info.visible = True
        mock_sym_info.return_value = mock_symbol_info

        mock_tick_info = MagicMock()
        mock_tick_info.ask = 1.08500
        mock_tick_info.bid = 1.08480
        mock_tick.return_value = mock_tick_info

        mock_result = MagicMock()
        mock_result.retcode = mt5.TRADE_RETCODE_DONE
        mock_result.order = 12347
        mock_result.volume = 1.00
        mock_result.price = 1.08500
        mock_result.comment = "Success"
        mock_order_send.return_value = mock_result

        # Execute
        res = self.executor.execute_signal("EURUSDm", "Buy")

        # Verify
        self.assertIsNotNone(res)
        self.assertEqual(res["retcode"], mt5.TRADE_RETCODE_DONE)
        self.assertEqual(res["order"], 12347)
        self.assertEqual(res["volume"], 1.00)
        self.assertIn("sl", res)
        self.assertGreater(res["sl"], 0.0)

        sent_request = mock_order_send.call_args[0][0]
        self.assertEqual(sent_request["volume"], 1.00)

