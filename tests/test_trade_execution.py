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


class TestMT5TradeExecutorCalibration(unittest.TestCase):
    """Per-pair calibrated sizing: USDJPY dynamic pip value + SL from ATR mults."""

    @patch("MetaTrader5.positions_get")
    @patch("MetaTrader5.terminal_info")
    @patch("MetaTrader5.symbol_info")
    @patch("MetaTrader5.symbol_info_tick")
    @patch("MetaTrader5.order_send")
    @patch("MetaTrader5.account_info")
    def test_usdjpy_live_sizing_uses_dynamic_pip_value(
        self, mock_acc_info, mock_order_send, mock_tick, mock_sym_info, mock_term_info, mock_positions
    ):
        import MetaTrader5 as mt5
        from axonai.default_config import DEFAULT_CONFIG, resolve_symbol_config

        cfg = resolve_symbol_config(DEFAULT_CONFIG, "USDJPY")
        cfg["realtime_dry_run"] = False
        cfg["realtime_max_lot"] = 1.0  # lift clamp so the computed lot is observable
        ex = MT5TradeExecutor(cfg)

        mock_term_info.return_value = True
        mock_positions.return_value = None
        mock_acc_info.return_value = MagicMock(equity=10000.0, balance=10000.0)

        si = MagicMock()
        si.visible = True
        si.digits = 3
        si.trade_contract_size = 100000
        mock_sym_info.return_value = si

        tk = MagicMock()
        tk.ask = 150.00
        tk.bid = 149.98
        mock_tick.return_value = tk

        res = MagicMock()
        res.retcode = mt5.TRADE_RETCODE_DONE
        res.order = 555
        res.volume = 0.25
        res.price = 150.00
        res.comment = "ok"
        mock_order_send.return_value = res

        # ATR = 0.30 price units (30 JPY-pips). SL = max(0.30*2.0, 16*0.01) = 0.60 → 60 pips.
        out = ex.send_order("USDJPY", mt5.ORDER_TYPE_BUY, live_state={"atr_14_h1": 0.30})
        self.assertIsNotNone(out)

        sent = mock_order_send.call_args[0][0]
        self.assertEqual(sent["magic"], 123458)  # per-pair magic
        # risk = 1% of 10000 = $100; pip_value = 100000*0.01/150 = 6.667; sl_pips = 60
        # lot = 100 / (60 * 6.667) = 0.25   (a constant $10 would wrongly give 0.17)
        self.assertAlmostEqual(sent["volume"], 0.25, places=2)
        # BUY SL is 60 pips (0.60) below entry 150.00
        self.assertAlmostEqual(sent["sl"], 149.40, places=2)

    @patch("MetaTrader5.positions_get")
    @patch("MetaTrader5.terminal_info")
    @patch("MetaTrader5.symbol_info")
    @patch("MetaTrader5.symbol_info_tick")
    @patch("MetaTrader5.order_send")
    @patch("MetaTrader5.account_info")
    def test_eurusd_live_sizing_unchanged(
        self, mock_acc_info, mock_order_send, mock_tick, mock_sym_info, mock_term_info, mock_positions
    ):
        import MetaTrader5 as mt5
        from axonai.default_config import DEFAULT_CONFIG, resolve_symbol_config

        cfg = resolve_symbol_config(DEFAULT_CONFIG, "EURUSD")
        cfg["realtime_dry_run"] = False
        cfg["realtime_max_lot"] = 1.0
        ex = MT5TradeExecutor(cfg)

        mock_term_info.return_value = True
        mock_positions.return_value = None
        mock_acc_info.return_value = MagicMock(equity=10000.0, balance=10000.0)

        si = MagicMock()
        si.visible = True
        si.digits = 5
        si.trade_contract_size = 100000
        mock_sym_info.return_value = si

        tk = MagicMock()
        tk.ask = 1.10000
        tk.bid = 1.09980
        mock_tick.return_value = tk

        res = MagicMock()
        res.retcode = mt5.TRADE_RETCODE_DONE
        res.order = 556
        res.volume = 0.0
        res.price = 1.10000
        res.comment = "ok"
        mock_order_send.return_value = res

        # ATR = 0.0030 (30 pips). SL = max(0.0030*2, 16*0.0001) = 0.0060 → 60 pips.
        ex.send_order("EURUSD", mt5.ORDER_TYPE_BUY, live_state={"atr_14_h1": 0.0030})
        sent = mock_order_send.call_args[0][0]
        self.assertEqual(sent["magic"], 123457)
        # risk $100; pip_value $10; sl_pips 60; lot = 100/(60*10) = 0.1667 → 0.17
        self.assertAlmostEqual(sent["volume"], 0.17, places=2)

