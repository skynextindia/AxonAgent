"""End-to-End mock integration test for AxonDaemon."""

import queue
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from axonai.realtime.daemon import AxonDaemon
from axonai.realtime.event_types import LiveCandle, MarketEvent, EventType, EventPriority


class TestDaemonE2E(unittest.TestCase):
    """End-to-end daemon loop execution test with mocked MT5."""

    def setUp(self):
        self.config = {
            "realtime_magic_number": 999999,
            "realtime_default_lot_size": 0.01,
            "realtime_deviation": 10,
            "realtime_cooldown_seconds": 5,
            "realtime_suppress_asian": False,
            "realtime_log_events": False,
            "realtime_dry_run": True,  # skip dynamic sizing in tests
            "test_mode": True,
            "realtime_calendar_enabled": False,
        }

    @patch("MetaTrader5.terminal_info")
    @patch("MetaTrader5.symbol_info")
    @patch("MetaTrader5.symbol_info_tick")
    @patch("MetaTrader5.order_send")
    @patch("MetaTrader5.positions_get")
    @patch("MetaTrader5.account_info")
    @patch("axonai.realtime.daemon.get_broker_tz_offset")
    @patch("axonai.realtime.daemon.LiveWorldState")
    @patch("axonai.realtime.daemon.LiveMarketEvidence")
    @patch("axonai.realtime.daemon.GraphExecutor")
    def test_daemon_full_flow(
        self, mock_graph, mock_evidence, mock_state, mock_tz_offset,
        mock_acc_info, mock_positions, mock_order_send, mock_tick_info, mock_sym_info, mock_term_info
    ):
        """Verify the full E2E flow from tick to event queue and signal execution."""
        mock_positions.return_value = ()
        mock_tz_offset.return_value = 2.0
        mock_term_info.return_value = True
        
        # Configure state mocks
        state_inst = mock_state.return_value
        state_inst.symbol = "EURUSD=X"
        state_inst.is_initialized = True
        
        inner_state = MagicMock()
        inner_state.dominant_regime = "trending"
        inner_state.regime_confidence = 0.85
        inner_state.volatility_regime = "high"
        inner_state.atr_14_h1 = 0.0015
        inner_state.spread_pips = 1.0
        inner_state.spread_safe = True
        inner_state.belief_score = 0.90
        inner_state.should_run_graph = True
        inner_state.session = "london"
        state_inst.snapshot.return_value = inner_state

        # Configure evidence mocks
        evidence_inst = mock_evidence.return_value
        evidence_inst._initialized = True
        evidence_inst._m15_candles = []
        evidence_inst._h1_candles = []
        evidence_inst._h4_candles = []
        
        inner_evidence = MagicMock()
        inner_evidence.swing_highs = []
        inner_evidence.swing_lows = []
        inner_evidence.key_levels = [1.15000]
        evidence_inst.snapshot.return_value = inner_evidence

        # Mock symbol info
        sym = MagicMock()
        sym.visible = True
        sym.digits = 5
        sym.trade_tick_size = 0.00001
        sym.trade_tick_value = 1.0
        sym.volume_min = 0.01
        sym.volume_max = 100.0
        sym.volume_step = 0.01
        mock_sym_info.side_effect = lambda s: sym if s == "EURUSDm" else None

        # Mock tick info
        tick = MagicMock()
        tick.ask = 1.15005
        tick.bid = 1.14995
        tick.time = int(datetime.now().timestamp())
        mock_tick_info.return_value = tick

        # Initialize Daemon
        daemon = AxonDaemon("EURUSD", self.config)
        daemon._start_time = datetime.now()
        daemon._running = True

        # Mock graph executor to return a BUY signal and stop the loop
        graph_inst = mock_graph.return_value
        graph_inst.seconds_until_ready = 0
        graph_inst.should_execute.return_value = True
        
        def stop_loop_and_return_buy(*args, **kwargs):
            daemon._running = False
            return MagicMock(), "Buy"
            
        graph_inst.execute.side_effect = stop_loop_and_return_buy

        # Mock account info
        acc = MagicMock()
        acc.equity = 10000.0
        acc.balance = 10000.0
        mock_acc_info.return_value = acc

        # Mock order result
        order_res = MagicMock()
        order_res.retcode = 10009  # TRADE_RETCODE_DONE
        order_res.order = 987654
        order_res.volume = 0.01
        order_res.price = 1.15005
        order_res.comment = "Success"
        mock_order_send.return_value = order_res

        # Create event to process in loop
        event = MarketEvent(
            event_type=EventType.LEVEL_BREACH,
            priority=EventPriority.HIGH,
            timestamp=datetime.now(),
            symbol="EURUSD=X",
            price=1.15005,
            details={"level": 1.15000}
        )

        # Ingest event directly into queue
        daemon.event_queue.put(event)

        # Let the loop execute
        daemon._event_loop()

        # Verify order request composition
        mock_order_send.assert_called_once()
        sent_req = mock_order_send.call_args[0][0]
        self.assertEqual(sent_req["symbol"], "EURUSDm")
        self.assertEqual(sent_req["type"], 0)  # BUY
        self.assertEqual(sent_req["price"], 1.15005)
        self.assertGreater(sent_req["sl"], 0.0)
        self.assertGreater(sent_req["tp"], 0.0)
        
        # Shutdown
        daemon.stop()
