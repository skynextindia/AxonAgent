"""Trade execution module for MetaTrader 5.

Performs live order routing, position size calculation, and execution via mt5.order_send().
"""

from __future__ import annotations

import logging
from typing import Dict, Any, Optional

from axonai.realtime.risk_guard import RiskGuard
from axonai.realtime.alerts import send_alert
from axonai.realtime.trade_phase import TradePhaseTracker
from axonai.realtime.exit_stats import ExitStats

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
        # Paper-trade mode: simulated fills, never sent to the broker.
        self.paper_trade = config.get("paper_trade", False)
        self._paper_ticket_seq = 0
        
        # Adaptive exit components
        self.phase_tracker = TradePhaseTracker(pip_mult=0.0001)
        self.exit_stats = ExitStats(csv_path="reports/exit_stats.csv")

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
        
        sl_atr_mult = self.config.get("realtime_sl_atr_multiple", self.config.get("sl_atr_multiple", 1.2))
        tp_atr_mult = self.config.get("realtime_tp_atr_multiple", self.config.get("tp_atr_multiple", 1.5))
        # SL is kept as sl_atr_mult*ATR to act as a hard backstop, while adaptive exit closes early
        sl_distance = max(atr * sl_atr_mult, 8 * pip)
        tp_distance = max(atr * tp_atr_mult, 16 * pip)

        # Spread guard: refuse entry when the live spread would eat too much of
        # the stop. Compared in price units (not pips) so it is symbol-agnostic.
        spread = tick.ask - tick.bid
        max_spread_frac = self.config.get("realtime_max_spread_frac", 0.5)
        if sl_distance > 0 and spread > max_spread_frac * sl_distance:
            logger.warning(
                "TradeExecutor: spread %.5f exceeds %.0f%% of stop %.5f — entry skipped for %s",
                spread, max_spread_frac * 100, sl_distance, symbol,
            )
            return {"success": False, "reason": "spread_too_wide",
                    "spread": round(spread, 5), "sl_distance": round(sl_distance, 5)}
        
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
            # SL distance (price units) converted to pips for sizing.
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

        # Paper-trade mode: simulate the fill and return WITHOUT touching the
        # live order API. Keeps signal→order logic fully exercised for tests
        # and is safe even against a funded account.
        if self.paper_trade or self.config.get("paper_trade", False):
            return self._simulate_fill(mt5, symbol, order_type, lot, price, sl, tp)

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

    def _simulate_fill(self, mt5, symbol: str, order_type, lot: float,
                       price: float, sl: float, tp: float) -> dict:
        """Simulate an instant fill at the requested price (paper-trade mode).

        Returns the same dict shape as a real fill so the daemon's position
        tracking, PnL logging and dashboard payloads work unchanged.
        """
        self._paper_ticket_seq += 1
        ticket = 900_000_000 + self._paper_ticket_seq  # synthetic, won't collide with broker tickets
        side = "BUY" if order_type == mt5.ORDER_TYPE_BUY else "SELL"
        logger.info(
            "TradeExecutor[PAPER]: Simulated %s fill | %s | vol=%.2f price=%.5f SL=%.5f TP=%.5f ticket=%d",
            side, symbol, lot, price, sl, tp, ticket,
        )
        send_alert(
            f"PAPER Trade: {symbol} | Type: {side} | Volume: {lot:.2f} | "
            f"Price: {price:.5f} | SL: {sl:.5f} | TP: {tp:.5f} | Ticket: {ticket}",
            self.config,
        )
        return {
            "retcode": mt5.TRADE_RETCODE_DONE,
            "comment": "PAPER",
            "volume": lot,
            "price": price,
            "bid": price,
            "ask": price,
            "order": ticket,
            "request_id": 0,
            "sl": sl,
            "tp": tp,
            "paper": True,
        }

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
