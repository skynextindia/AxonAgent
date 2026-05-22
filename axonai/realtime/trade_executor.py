"""Trade execution module for MetaTrader 5.

Performs live order routing, position size calculation, and execution via mt5.order_send().
"""

from __future__ import annotations

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class MT5TradeExecutor:
    """Handles sending order requests to MetaTrader 5."""

    def __init__(self, config: dict):
        self.config = config
        self.magic = config.get("realtime_magic_number", 123456)
        self.deviation = config.get("realtime_deviation", 20)
        self.default_lot_size = config.get("realtime_default_lot_size", 0.01)

    def execute_signal(self, symbol: str, signal: str) -> Optional[dict]:
        """Convert a 5-tier signal into an MT5 order action.

        Signals: Buy, Overweight, Hold, Underweight, Sell
        """
        import MetaTrader5 as mt5

        logger.info("TradeExecutor: Evaluating signal: %s for %s", signal, symbol)

        if signal in ["Buy", "Overweight"]:
            return self.send_order(symbol, mt5.ORDER_TYPE_BUY)
        elif signal in ["Sell", "Underweight"]:
            return self.send_order(symbol, mt5.ORDER_TYPE_SELL)
        else:
            logger.info("TradeExecutor: Signal is %s. No order action taken (HOLD).", signal)
            return None

    def send_order(self, symbol: str, order_type: int) -> Optional[dict]:
        """Send a market order to MT5."""
        import MetaTrader5 as mt5

        # Check connection
        if not mt5.terminal_info():
            logger.error("TradeExecutor: Not connected to MT5 terminal.")
            return None

        # Check symbol info
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            logger.error("TradeExecutor: Symbol %s not found.", symbol)
            return None

        if not symbol_info.visible:
            if not mt5.symbol_select(symbol, True):
                logger.error("TradeExecutor: Failed to select symbol %s.", symbol)
                return None

        # Determine price & volume
        lot = self.default_lot_size
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error("TradeExecutor: Failed to get tick for %s.", symbol)
            return None

        price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

        # Prepare request
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": f"AxonAI {order_type} execution",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }

        # Send request
        logger.info("TradeExecutor: Sending order request: %s", request)
        result = mt5.order_send(request)
        if result is None:
            logger.error("TradeExecutor: order_send returned None")
            return None

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("TradeExecutor: Order failed. Retcode: %d, Comment: %s",
                         result.retcode, result.comment)
            # Try with another filling type if FOK fails (e.g. IOC)
            if result.retcode in [mt5.TRADE_RETCODE_INVALID_FILL, mt5.TRADE_RETCODE_LIMIT_VOLUME]:
                logger.info("TradeExecutor: Retrying with ORDER_FILLING_IOC...")
                request["type_filling"] = mt5.ORDER_FILLING_IOC
                result = mt5.order_send(request)
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info("TradeExecutor: Order successful on retry! Ticket: %d", result.order)
                    return self._result_to_dict(result)
            return self._result_to_dict(result)

        logger.info("TradeExecutor: Order executed successfully! Ticket: %d", result.order)
        return self._result_to_dict(result)

    def _result_to_dict(self, result) -> dict:
        """Helper to convert OrderSendResult to a dictionary."""
        return {
            "retcode": result.retcode,
            "comment": result.comment,
            "volume": result.volume,
            "price": result.price,
            "bid": result.bid,
            "ask": result.ask,
            "order": result.order,
            "request_id": result.request_id,
        }
