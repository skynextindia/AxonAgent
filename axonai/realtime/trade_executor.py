"""Trade execution module for MetaTrader 5.

Performs live order routing, position size calculation, and execution via mt5.order_send().
"""

from __future__ import annotations

import logging
from typing import Dict, Any, Optional

from axonai.realtime.risk_guard import RiskGuard
from axonai.realtime.alerts import send_alert
from axonai.dataflows.mt5_data import mt5_lock

logger = logging.getLogger(__name__)


class MT5TradeExecutor:
    """Handles sending order requests to MetaTrader 5."""

    def __init__(self, config: dict, risk_guard=None):
        self.config = config
        self.magic = config.get("realtime_magic_number", 123456)
        self.deviation = config.get("realtime_deviation", 20)
        self.default_lot_size = config.get("realtime_default_lot_size", 0.01)
        # Shared, account-global RiskGuard when the supervisor provides one (a
        # single daily-drawdown breaker across all pairs); otherwise build ours.
        self.risk_guard = risk_guard if risk_guard is not None else RiskGuard(config)
        self.circuit_breaker = self.risk_guard

    def execute_signal(self, symbol: str, signal: str, live_state: Optional[Any] = None,
                       size_scale: float = 1.0) -> Optional[dict]:
        """Convert a 5-tier signal into an MT5 order action.

        Signals: Buy, Overweight, Hold, Underweight, Sell
        ``size_scale`` (1.0 = unchanged) multiplies the final lot; the
        correlation engine uses it to shrink correlated exposure (Phase 4).
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
            return self.send_order(symbol, mt5.ORDER_TYPE_BUY, live_state, size_scale)
        elif signal in ["Sell", "Underweight"]:
            return self.send_order(symbol, mt5.ORDER_TYPE_SELL, live_state, size_scale)
        else:
            logger.info("TradeExecutor: Signal is %s. No order action taken (HOLD).", signal)
            return None

    def send_order(self, symbol: str, order_type: int, live_state: Optional[Any] = None,
                   size_scale: float = 1.0) -> Optional[dict]:
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

        # Position conflict guard: cap at 1 open position per strategy. Filter by
        # symbol AND magic so concurrent per-pair daemons never cross-block.
        existing = mt5.positions_get(symbol=symbol)
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
        pip = self.config.get("pip_size") or (
            0.01 if ("JPY" in symbol.upper() or "XAU" in symbol.upper()) else 0.0001
        )

        sl_mult = self.config.get("sl_atr_mult", 2.0)
        tp_mult = self.config.get("tp_atr_mult", 2.0)
        min_stop_pips = self.config.get("min_stop_pips", 16.0)
        sl_distance = max(atr * sl_mult, min_stop_pips * pip)
        tp_distance = max(atr * tp_mult, min_stop_pips * pip)
        
        sl = entry - sl_distance if direction == "BUY" else entry + sl_distance
        tp = entry + tp_distance if direction == "BUY" else entry - tp_distance

        # Format price to correct number of digits
        digits = getattr(symbol_info, "digits", 5)
        if not isinstance(digits, int):
            digits = 5
        sl = round(sl, digits)
        tp = round(tp, digits)
        price = round(price, digits)

        # 3. Risk-based dynamic position sizing for BOTH pairs. The lot is derived
        # from account equity, the configured risk %, and the actual stop distance,
        # so every trade risks the same slice of the account regardless of pair.
        # This is NOT gated by realtime_dry_run: dry-run never blocked real order
        # routing here, it only forced a misleading fixed 1.0 lot (which left the
        # lead pair EURUSD stuck at 1.0 while followers were shrunk by the
        # correlation size_scale below). Sizing now runs for EURUSD and USDJPY alike.
        # Execution lot bounds — apply to EVERY pair. Dynamic risk-sizing scales
        # UP from ``min_lot`` (>= 1.0 by default) and is capped at ``max_lot``.
        min_lot = self.config.get("realtime_min_lot", 1.0)
        max_lot = self.config.get("realtime_max_lot", 0.10)

        acc = mt5.account_info()
        if acc:
            account_equity = acc.equity if acc else 10000.0
            risk_pct = self.config.get("realtime_risk_pct", 0.01)  # risk_pct from config default 0.01
            risk_amount = account_equity * risk_pct
            # Actual stop distance in pips (SL was computed above as sl_distance).
            sl_pips = max(sl_distance / pip, 1.0)
            # $/pip/lot: pinned by config for USD-quote pairs (~$10), or derived
            # from the live price for USD-base pairs (e.g. USDJPY ≈ $6–7).
            pip_value_per_lot = self._pip_value_per_lot(symbol_info, price, pip)
            risk_lot = round(risk_amount / (sl_pips * pip_value_per_lot), 2)
            # Floor at min_lot (>= 1.0 lot for every pair), cap at max_lot.
            lot_size = max(min_lot, min(risk_lot, max_lot))
            lot = lot_size

            logger.info(
                "TradeExecutor: Account equity: %.2f | Risk amount: %.2f | SL pips: %.2f | "
                "Risk lot: %.4f | bounds [%.2f, %.2f] | Final lot: %.2f",
                account_equity, risk_amount, sl_pips, risk_lot, min_lot, max_lot, lot
            )
        else:
            lot = max(min_lot, self.default_lot_size)
            logger.info("TradeExecutor: No account info; using min-lot floor: %.2f", lot)

        # Correlation-driven position-size scaling (Phase 4); 1.0 = unchanged.
        # Never let the scaled lot fall below the min_lot execution floor.
        if size_scale != 1.0:
            lot = max(min_lot, round(lot * size_scale, 2))

        # Prop-account risk caps (config-gated; default off, node-only in practice).
        # Trim the final lot so THIS trade's stop-risk <= per-trade ceiling AND the
        # TOTAL open stop-risk (this trade + already-open positions) <= combined
        # ceiling. Shrink to fit; block only if even min_lot won't fit the budget.
        cap_pt = self.config.get("risk_cap_per_trade_pct")
        cap_cb = self.config.get("risk_cap_combined_pct")
        if acc and (cap_pt or cap_cb):
            per_pip_risk = sl_pips * pip_value_per_lot          # $ risk per 1.0 lot
            open_risk = self._account_open_risk_usd() if cap_cb else 0.0
            lot, blocked, budget = self._apply_risk_caps(
                lot, min_lot, per_pip_risk, account_equity, cap_pt, cap_cb, open_risk)
            if blocked:
                logger.info("TradeExecutor: %s entry BLOCKED by combined risk cap %.1f%% "
                            "(open risk $%.0f, remaining budget $%.0f < min-lot risk $%.0f)",
                            symbol, cap_cb or 0.0, open_risk, max(0.0, budget),
                            min_lot * per_pip_risk)
                try:
                    send_alert(f"Risk cap: {symbol} entry blocked — open risk ${open_risk:.0f} "
                               f"near {cap_cb}% combined cap.", self.config)
                except Exception:
                    pass
                return None

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

        # Send request (serialized across daemon threads via the shared MT5 lock)
        logger.info("TradeExecutor: Sending order request with SL/TP: %s", request)
        with mt5_lock:
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
                with mt5_lock:
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

    def _pip_value_per_lot(self, symbol_info, price: float, pip: float) -> float:
        """Return $/pip for a 1.0-lot position, in the account currency (USD).

        Config key ``realtime_pip_value_per_lot`` pins the value when truthy
        (USD-quote pairs like EURUSD ≈ $10). When None/0 (USD-base pairs like
        USDJPY, whose pip value is price-dependent) it is derived from the
        contract size and current price: contract * pip / price.
        """
        configured = self.config.get("realtime_pip_value_per_lot", 10.0)
        if configured:
            return float(configured)
        contract = getattr(symbol_info, "trade_contract_size", 100000) or 100000
        val = contract * pip
        if price and price > 0:
            val = val / price  # USD-base pair: convert quote-currency pip → USD
        return max(val, 0.01)

    @staticmethod
    def _apply_risk_caps(lot, min_lot, per_pip_risk, equity, cap_pt_pct, cap_cb_pct, open_risk_usd):
        """Trim `lot` to fit the per-trade and combined stop-risk ceilings.

        Returns (lot, blocked, budget_usd). The dollar budget for THIS trade is
        min(per-trade %, combined % − already-open risk). `blocked` is True when
        even `min_lot` exceeds that budget, so the caller should skip the entry.
        Percentages are given as numbers (1.5 == 1.5%).
        """
        budget = float("inf")
        if cap_pt_pct:
            budget = min(budget, equity * (cap_pt_pct / 100.0))
        if cap_cb_pct:
            budget = min(budget, equity * (cap_cb_pct / 100.0) - open_risk_usd)
        if budget == float("inf") or per_pip_risk <= 0:
            return lot, False, budget
        cap_lot = budget / per_pip_risk
        if cap_lot < min_lot - 1e-9:
            return 0.0, True, budget
        return round(min(lot, cap_lot), 2), False, budget

    def _account_open_risk_usd(self) -> float:
        """Total stop-loss $-risk across ALL open positions on this account.

        Per position: max(0, entry→SL distance in pips) × $/pip/lot × volume. A
        position whose SL has trailed to/past breakeven contributes 0 (no
        downside left), which frees combined-cap budget for a new entry. USD-quote
        pairs use $10/pip/lot; USD-base (JPY) derive from the live price.
        """
        import MetaTrader5 as mt5
        try:
            positions = mt5.positions_get()
        except Exception as e:
            logger.warning("open-risk: positions_get failed: %s", e)
            return 0.0
        if not positions:
            return 0.0
        total = 0.0
        for p in positions:
            sl = getattr(p, "sl", 0.0) or 0.0
            if sl <= 0.0:
                continue
            sym = p.symbol.upper()
            pipsz = 0.01 if ("JPY" in sym or "XAU" in sym) else 0.0001
            if p.type == mt5.POSITION_TYPE_BUY:
                risk_pips = (p.price_open - sl) / pipsz
            else:
                risk_pips = (sl - p.price_open) / pipsz
            if risk_pips <= 0:
                continue                                   # SL at/past breakeven
            px = getattr(p, "price_current", 0.0) or p.price_open
            pipval = (100000.0 * pipsz / px) if "JPY" in sym else 10.0
            total += risk_pips * pipval * p.volume
        return total

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
