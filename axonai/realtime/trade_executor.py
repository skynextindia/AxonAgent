"""Trade execution module for MetaTrader 5.

Performs live order routing, position size calculation, and execution via mt5.order_send().
"""

from __future__ import annotations

import logging
from typing import Dict, Any, Optional

from axonai.realtime.risk_guard import RiskGuard
from axonai.realtime.alerts import send_alert

logger = logging.getLogger(__name__)


class MT5TradeExecutor:
    """Handles sending order requests to MetaTrader 5."""

    def __init__(self, config: dict):
        self.config = config
        self.magic = config.get("realtime_magic_number", 123456)
        self.deviation = config.get("realtime_deviation", 20)
        self.default_lot_size = config.get("realtime_default_lot_size", 0.01)
        self.risk_guard = RiskGuard(config)
        self.circuit_breaker = self.risk_guard

    def execute_signal(self, symbol: str, signal: str, live_state: Optional[Any] = None) -> Optional[dict]:
        """Convert a 5-tier signal into an MT5 order action.

        Signals: Buy, Overweight, Hold, Underweight, Sell
        """
        import MetaTrader5 as mt5

        logger.info("TradeExecutor: Evaluating signal: %s for %s", signal, symbol)

        # Drawdown circuit breaker check
        if mt5 and mt5.terminal_info():
            acc = mt5.account_info()
            if acc:
                self.risk_guard.update_equity(acc.equity, acc.balance)

        if self.circuit_breaker.is_tripped:
            logger.warning("CIRCUIT BREAKER ACTIVE — trade rejected")
            return {"success": False, "reason": "circuit_breaker_tripped"}

        if signal in ["Buy", "Overweight"]:
            return self.send_order(symbol, mt5.ORDER_TYPE_BUY, live_state)
        elif signal in ["Sell", "Underweight"]:
            return self.send_order(symbol, mt5.ORDER_TYPE_SELL, live_state)
        else:
            logger.info("TradeExecutor: Signal is %s. No order action taken (HOLD).", signal)
            return None

    def send_order(self, symbol: str, order_type: int, live_state: Optional[Any] = None) -> Optional[dict]:
        """Send a market order with dynamic SL/TP and position sizing to MT5."""
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

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error("TradeExecutor: Failed to get tick for %s.", symbol)
            return None

        price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

        # Position conflict guard: enforce cap of maximum 1 open position per strategy (by magic number)
        existing = mt5.positions_get()
        if existing:
            # Filter by magic number to allow concurrent systems to trade independently
            system_existing = [p for p in existing if p.magic == self.magic]
            if len(system_existing) >= 1:
                logger.info("TradeExecutor: Position already open for magic %d. Skipping new order.", self.magic)
                return None

        # 1. Fetch H1 ATR for SL/TP calculations
        atr = 0.0
        if live_state is not None:
            if hasattr(live_state, "snapshot"):
                snap = live_state.snapshot()
                atr = getattr(snap, "atr_14_h1", 0.0)
            elif hasattr(live_state, "atr_14_h1"):
                atr = getattr(live_state, "atr_14_h1", 0.0)
            elif isinstance(live_state, dict):
                atr = live_state.get("atr_14_h1", 0.0)

        # Fallback if ATR is unavailable or zero
        if atr <= 0.0:
            atr = price * 0.0015  # default to 0.15% of price
            logger.info("TradeExecutor: ATR unavailable. Using fallback value: %.5f", atr)

        # 2. Calculate ATR-based Stop Loss & Take Profit exactly as requested
        entry = price
        direction = "BUY" if order_type == mt5.ORDER_TYPE_BUY else "SELL"
        pip = 0.01 if "JPY" in symbol.upper() else 0.0001
        
        sl_distance = max(atr * 1.0, 8 * pip)
        tp_distance = max(atr * 2.0, 16 * pip)
        
        sl = entry - sl_distance if direction == "BUY" else entry + sl_distance
        tp = entry + tp_distance if direction == "BUY" else entry - tp_distance

        # Format price to correct number of digits
        digits = getattr(symbol_info, "digits", 5)
        if not isinstance(digits, int):
            digits = 5
        sl = round(sl, digits)
        tp = round(tp, digits)
        price = round(price, digits)

        # 3. Dynamic Position Sizing based on Account Equity & Risk Percentage exactly as requested
        acc = mt5.account_info()
        is_mock_env = self.config.get("realtime_dry_run", False)
        
        if is_mock_env:
            lot = 1.00
            logger.info("TradeExecutor: Dryrun active. Using fixed lot size: 1.00")
        elif acc:
            account_equity = acc.equity if acc else 10000.0
            risk_pct = self.config.get("realtime_risk_pct", 0.01)  # risk_pct from config default 0.01
            risk_amount = account_equity * risk_pct
            sl_pips = sl_distance / pip
            lot_size = round(risk_amount / (sl_pips * 0.10), 2)
            lot_size = max(0.01, min(lot_size, 0.10))  # hard limits
            lot = lot_size
            
            logger.info(
                "TradeExecutor: Account equity: %.2f | Risk amount: %.2f | "
                "SL pips: %.2f | Calculated lot: %.4f | Final lot: %.2f",
                account_equity, risk_amount, sl_pips, lot_size, lot
            )
        else:
            lot = self.default_lot_size
            logger.info("TradeExecutor: Using default lot size: %.2f", lot)

        # Prepare request
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": f"AxonAI {order_type} execution",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }

        # Send request
        logger.info("TradeExecutor: Sending order request with SL/TP: %s", request)
        result = mt5.order_send(request)
        if result is None:
            logger.error("TradeExecutor: order_send returned None")
            return None

        # Track PnL if trade fails or succeeds
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("TradeExecutor: Order failed. Retcode: %d, Comment: %s",
                         result.retcode, result.comment)
            
            # Send alert
            send_alert(
                f"Trade FAILED: {symbol} | Type: {'BUY' if order_type == mt5.ORDER_TYPE_BUY else 'SELL'} "
                f"| Retcode: {result.retcode} | Comment: {result.comment}",
                self.config
            )
            
            # Try with another filling type if FOK fails (e.g. IOC)
            if result.retcode in [mt5.TRADE_RETCODE_INVALID_FILL, mt5.TRADE_RETCODE_LIMIT_VOLUME]:
                logger.info("TradeExecutor: Retrying with ORDER_FILLING_IOC...")
                request["type_filling"] = mt5.ORDER_FILLING_IOC
                result = mt5.order_send(request)
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info("TradeExecutor: Order successful on retry! Ticket: %d", result.order)
                    send_alert(
                        f"Trade Executed on Retry: {symbol} | Type: {'BUY' if order_type == mt5.ORDER_TYPE_BUY else 'SELL'} "
                        f"| Volume: {lot:.2f} | Price: {price:.5f} | SL: {sl:.5f} | TP: {tp:.5f} | Ticket: {result.order}",
                        self.config
                    )
                    return self._result_to_dict(result, sl)
            return self._result_to_dict(result, sl)

        logger.info("TradeExecutor: Order executed successfully! Ticket: %d", result.order)
        send_alert(
            f"Trade Executed: {symbol} | Type: {'BUY' if order_type == mt5.ORDER_TYPE_BUY else 'SELL'} "
            f"| Volume: {lot:.2f} | Price: {price:.5f} | SL: {sl:.5f} | TP: {tp:.5f} | Ticket: {result.order}",
            self.config
        )
        return self._result_to_dict(result, sl)

    def _result_to_dict(self, result, sl: float = 0.0) -> dict:
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
            "sl": sl,
        }
