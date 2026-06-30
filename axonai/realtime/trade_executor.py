"""Trade execution module for MetaTrader 5.

Performs live order routing, position size calculation, and execution via mt5.order_send().
"""

from __future__ import annotations

import logging
from typing import Dict, Any, Optional

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

from axonai.dataflows.mt5_data import get_mt5_trade
from axonai.realtime.risk_guard import RiskGuard
from axonai.realtime.alerts import send_alert
from axonai.realtime.trade_phase import TradePhaseTracker
from axonai.realtime.exit_stats import ExitStats

logger = logging.getLogger(__name__)

# Constants mapping for environments without MetaTrader5 (like WSL)
ORDER_TYPE_BUY = 0 if mt5 is None else mt5.ORDER_TYPE_BUY
ORDER_TYPE_SELL = 1 if mt5 is None else mt5.ORDER_TYPE_SELL
TRADE_ACTION_DEAL = 1 if mt5 is None else mt5.TRADE_ACTION_DEAL
ORDER_TIME_GTC = 0 if mt5 is None else mt5.ORDER_TIME_GTC
ORDER_FILLING_FOK = 0 if mt5 is None else mt5.ORDER_FILLING_FOK
ORDER_FILLING_IOC = 1 if mt5 is None else mt5.ORDER_FILLING_IOC
TRADE_RETCODE_DONE = 10009 if mt5 is None else mt5.TRADE_RETCODE_DONE
TRADE_RETCODE_INVALID_FILL = 10014 if mt5 is None else mt5.TRADE_RETCODE_INVALID_FILL
TRADE_RETCODE_LIMIT_VOLUME = 10018 if mt5 is None else mt5.TRADE_RETCODE_LIMIT_VOLUME


class MT5TradeExecutor:
    """Handles sending order requests to MetaTrader 5 (either directly or via Execution Bridge)."""

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

    def _get_mt5(self):
        """Get MT5 instance: prefer trade terminal (dual-terminal), fall back to main MT5."""
        mt5_trade = get_mt5_trade()
        if mt5_trade:
            return mt5_trade
        return mt5

    def execute_signal(self, symbol: str, signal: str, live_state: Optional[Any] = None) -> Optional[dict]:
        """Convert a 5-tier signal into an MT5 order action.

        Signals: Buy, Overweight, Hold, Underweight, Sell
        """
        logger.info("TradeExecutor: Evaluating signal: %s for %s", signal, symbol)

        # Drawdown circuit breaker check
        is_bridge = self.config.get("realtime_execution_mode", "direct") == "bridge"
        if not is_bridge:
            mt5_inst = self._get_mt5()
            if mt5_inst and mt5_inst.terminal_info():
                acc = mt5_inst.account_info()
                if acc:
                    self.risk_guard.update_equity(acc.equity, acc.balance)

        if self.circuit_breaker.is_tripped:
            logger.warning("CIRCUIT BREAKER ACTIVE — trade rejected")
            return {"success": False, "reason": "circuit_breaker_tripped"}

        if signal in ["Buy", "Overweight"]:
            return self.send_order(symbol, ORDER_TYPE_BUY, live_state)
        elif signal in ["Sell", "Underweight"]:
            return self.send_order(symbol, ORDER_TYPE_SELL, live_state)
        else:
            logger.info("TradeExecutor: Signal is %s. No order action taken (HOLD).", signal)
            return None

    def send_order(self, symbol: str, order_type: int, live_state: Optional[Any] = None) -> Optional[dict]:
        """Send a market order with dynamic SL/TP and position sizing to MT5."""
        is_bridge = self.config.get("realtime_execution_mode", "direct") == "bridge"

        # Check connection & symbol info if running directly
        if is_bridge:
            from axonai.realtime.execution_client import send_execution_command
            if live_state:
                if hasattr(live_state, "current_bid") and live_state.current_bid:
                    bid = live_state.current_bid
                    ask = live_state.current_ask
                elif isinstance(live_state, dict):
                    bid = live_state.get("bid", 1.1000)
                    ask = live_state.get("ask", 1.1005)
                else:
                    bid = 1.1000
                    ask = 1.1005
            else:
                bid = 1.1000
                ask = 1.1005
        else:
            mt5_inst = self._get_mt5()
            if mt5_inst is None or not mt5_inst.terminal_info():
                logger.error("TradeExecutor: Not connected to MT5 terminal.")
                return None

            symbol_info = mt5_inst.symbol_info(symbol)
            if symbol_info is None:
                logger.error("TradeExecutor: Symbol %s not found.", symbol)
                return None

            if not symbol_info.visible:
                if not mt5_inst.symbol_select(symbol, True):
                    logger.error("TradeExecutor: Failed to select symbol %s.", symbol)
                    return None

            tick = mt5_inst.symbol_info_tick(symbol)
            if tick is None:
                logger.error("TradeExecutor: Failed to get tick for %s.", symbol)
                return None
            bid = tick.bid
            ask = tick.ask

        price = ask if order_type == ORDER_TYPE_BUY else bid

        # Position conflict guard: enforce cap of maximum 1 open position per strategy (by magic number)
        if is_bridge:
            res = send_execution_command(self.config, {"action": "positions_get", "symbol": symbol, "magic": self.magic})
            existing_count = len(res.get("positions", [])) if res.get("success", False) else 0
            if existing_count >= 1:
                logger.info("TradeExecutor: Position already open for magic %d on execution bridge. Skipping new order.", self.magic)
                return None
        else:
            mt5_inst = self._get_mt5()
            existing = mt5_inst.positions_get()
            if existing:
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

        # 2. Calculate ATR-based Stop Loss & PLACEHOLDER Take Profit
        # SL remains fixed (hard backstop). TP is a wide placeholder — ExitEngine drives actual exits.
        entry = price
        direction = "BUY" if order_type == ORDER_TYPE_BUY else "SELL"
        
        # Determine pip size and digits dynamically
        if not is_bridge and mt5_inst and mt5_inst.symbol_info(symbol):
            s_info = mt5_inst.symbol_info(symbol)
            pip = s_info.point * 10
            digits = s_info.digits
        else:
            if "JPY" in symbol.upper():
                pip, digits = 0.01, 3
            elif price > 1000:
                pip, digits = 0.1, 2
            else:
                pip, digits = 0.0001, 5

        sl_atr_mult = self.config.get("realtime_sl_atr_multiple", self.config.get("sl_atr_multiple", 1.2))
        # SL is kept as sl_atr_mult*ATR to act as a hard backstop
        sl_distance = max(atr * sl_atr_mult, 8 * pip)

        # TP is now a placeholder (unreachable) — ExitEngine will close trades, not TP price
        placeholder_tp_mult = self.config.get("placeholder_tp_sl_multiple", 3.0)
        tp_distance = max(atr * sl_atr_mult * placeholder_tp_mult, 16 * pip)

        # Spread guard: refuse entry when the live spread would eat too much of the stop
        spread = ask - bid
        max_spread_frac = self.config.get("realtime_max_spread_frac", 0.5)
        if sl_distance > 0 and spread > max_spread_frac * sl_distance:
            logger.warning(
                "TradeExecutor: spread %.5f exceeds %.0f%% of stop %.5f — entry skipped for %s",
                spread, max_spread_frac * 100, sl_distance, symbol,
            )
            return {"success": False, "reason": "spread_too_wide",
                    "spread": round(spread, 5), "sl_distance": round(sl_distance, 5)}

        sl = entry - sl_distance if direction == "BUY" else entry + sl_distance
        tp = entry + tp_distance if direction == "BUY" else entry - tp_distance  # Placeholder — ExitEngine drives exits

        # Format price to correct number of digits
        sl = round(sl, digits)
        tp = round(tp, digits)
        price = round(price, digits)

        # 3. Position Sizing
        #    - dry-run: fixed 1.00 lot (sandbox)
        #    - live + realtime_dynamic_sizing: risk-based off account equity
        #    - live (default): the configured realtime_default_lot_size (predictable)
        is_mock_env = self.config.get("realtime_dry_run", False)

        if is_mock_env:
            lot = 1.00
            logger.info("TradeExecutor: Dryrun active. Using fixed lot size: 1.00")
        elif self.config.get("realtime_dynamic_sizing", False):
            if is_bridge:
                res = send_execution_command(self.config, {"action": "account_info"})
                equity = res.get("equity", 10000.0) if res.get("success") else 10000.0
            else:
                mt5_inst = self._get_mt5()
                acc = mt5_inst.account_info() if mt5_inst else None
                equity = acc.equity if acc else 10000.0

            risk_pct = self.config.get("realtime_risk_pct", 0.01)
            calculated_risk = equity * risk_pct
            
            # Hard cap the risk per trade to 100 USD (configurable) to prevent massive losses
            max_risk_usd = self.config.get("realtime_max_risk_usd", 100.0)
            risk_amount = min(calculated_risk, max_risk_usd)
            
            sl_pips = sl_distance / pip

            # Validate SL is meaningful
            if sl_pips < 1.0:
                logger.warning("TradeExecutor: SL distance too small (%.4f pips) - entry skipped", sl_pips)
                return {"success": False, "reason": "sl_too_small", "sl_pips": round(sl_pips, 4)}

            # Calculate exact pip value per lot
            pip_value_per_lot = 10.0 # fallback
            tick_value = 0.0
            tick_size = 0.0

            if is_bridge:
                sym_res = send_execution_command(self.config, {"action": "symbol_info", "symbol": symbol})
                if sym_res.get("success"):
                    tick_value = sym_res.get("trade_tick_value", 0.0)
                    tick_size = sym_res.get("trade_tick_size", 0.0)
            else:
                if mt5_inst and mt5_inst.symbol_info(symbol):
                    s_info = mt5_inst.symbol_info(symbol)
                    tick_value = s_info.trade_tick_value
                    tick_size = s_info.trade_tick_size

            if tick_value > 0 and tick_size > 0:
                # pip_value_per_lot = how much 1 lot profits when price moves by 1 pip
                pip_value_per_lot = (pip / tick_size) * tick_value
            elif price > 1000:
                # Fallbacks for non-forex if tick value is unavailable
                pip_value_per_lot = 1.0 if pip >= 0.1 else 10.0
            
            lot_size = round(risk_amount / max(sl_pips * pip_value_per_lot, 1e-6), 2)
            max_lot = self.config.get("realtime_max_lot", 50.0)  # Raised from 1.0 to 50.0 to support 100k+ accounts
            lot = max(0.01, min(lot_size, max_lot))

            logger.info(
                "TradeExecutor: Dynamic sizing | equity: %.2f | risk: %.2f | "
                "SL pips: %.2f | pip val: $%.2f | calc lot: %.4f | final lot: %.2f",
                equity, risk_amount, sl_pips, pip_value_per_lot, lot_size, lot
            )
        else:
            lot = self.default_lot_size
            logger.info("TradeExecutor: Using configured default lot size: %.2f", lot)

        # Paper-trade mode: simulate the fill and return without live order API.
        if self.paper_trade or self.config.get("paper_trade", False):
            return self._simulate_fill(symbol, order_type, lot, price, sl, tp)

        if is_bridge:
            logger.info("TradeExecutor: Sending order request to execution bridge: %s", {
                "symbol": symbol, "type": order_type, "volume": lot, "price": price, "sl": sl, "tp": tp
            })
            result = send_execution_command(self.config, {
                "action": "open",
                "symbol": symbol,
                "type": order_type,
                "volume": lot,
                "price": price,
                "sl": sl,
                "tp": tp,
                "magic": self.magic,
                "deviation": self.deviation
            })
            if result.get("success"):
                logger.info("TradeExecutor: Execution bridge order executed successfully! Ticket: %d", result.get("order"))
                send_alert(
                    f"Trade Executed (Bridge): {symbol} | Type: {'BUY' if order_type == ORDER_TYPE_BUY else 'SELL'} "
                    f"| Volume: {lot:.2f} | Price: {price:.5f} | SL: {sl:.5f} | TP: {tp:.5f} | Ticket: {result.get('order')}",
                    self.config
                )
                return result
            else:
                logger.error("TradeExecutor: Execution bridge order failed. Reason: %s", result.get("reason"))
                send_alert(f"Trade FAILED (Bridge): {symbol} | Type: {'BUY' if order_type == ORDER_TYPE_BUY else 'SELL'} | Reason: {result.get('reason')}", self.config)
                return None
        else:
            # Prepare request for direct MT5
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

            logger.info("TradeExecutor: Sending order request with SL/TP: %s", request)
            mt5_inst = self._get_mt5()
            result = mt5_inst.order_send(request) if mt5_inst else None
            if result is None:
                logger.error("TradeExecutor: order_send returned None")
                return None

            # Track PnL if trade fails or succeeds
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                logger.error("TradeExecutor: Order failed. Retcode: %d, Comment: %s",
                             result.retcode, result.comment)
                
                send_alert(
                    f"Trade FAILED: {symbol} | Type: {'BUY' if order_type == ORDER_TYPE_BUY else 'SELL'} "
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
                            f"Trade Executed on Retry: {symbol} | Type: {'BUY' if order_type == ORDER_TYPE_BUY else 'SELL'} "
                            f"| Volume: {lot:.2f} | Price: {price:.5f} | SL: {sl:.5f} | TP: {tp:.5f} | Ticket: {result.order}",
                            self.config
                        )
                        return self._result_to_dict(result, sl)
                return self._result_to_dict(result, sl)

            logger.info("TradeExecutor: Order executed successfully! Ticket: %d", result.order)
            send_alert(
                f"Trade Executed: {symbol} | Type: {'BUY' if order_type == ORDER_TYPE_BUY else 'SELL'} "
                f"| Volume: {lot:.2f} | Price: {price:.5f} | SL: {sl:.5f} | TP: {tp:.5f} | Ticket: {result.order}",
                self.config
            )
            return self._result_to_dict(result, sl)

    def _simulate_fill(self, symbol: str, order_type: int, lot: float,
                       price: float, sl: float, tp: float) -> dict:
        """Simulate an instant fill at the requested price (paper-trade mode)."""
        self._paper_ticket_seq += 1
        ticket = 900_000_000 + self._paper_ticket_seq
        side = "BUY" if order_type == ORDER_TYPE_BUY else "SELL"
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
            "retcode": TRADE_RETCODE_DONE,
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
            "success": True,
        }

    def _result_to_dict(self, result, sl: float = 0.0) -> dict:
        """Helper to convert OrderSendResult to a dictionary."""
        return {
            "success": result.retcode == TRADE_RETCODE_DONE,
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
