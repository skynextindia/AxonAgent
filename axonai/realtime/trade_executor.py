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
from axonai.realtime.portfolio_guard import PortfolioGuard
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
# A partial fill means a REAL position was opened on the broker — it must be
# treated as success, not failure, or the system leaves an unmanaged live position.
TRADE_RETCODE_DONE_PARTIAL = 10010 if mt5 is None else getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010)
TRADE_RETCODE_INVALID_FILL = 10014 if mt5 is None else mt5.TRADE_RETCODE_INVALID_FILL
TRADE_RETCODE_LIMIT_VOLUME = 10018 if mt5 is None else mt5.TRADE_RETCODE_LIMIT_VOLUME
ORDER_TYPE_BUY_LIMIT = 2 if mt5 is None else mt5.ORDER_TYPE_BUY_LIMIT
ORDER_TYPE_SELL_LIMIT = 3 if mt5 is None else mt5.ORDER_TYPE_SELL_LIMIT
TRADE_ACTION_PENDING = 2 if mt5 is None else mt5.TRADE_ACTION_PENDING
ORDER_FILLING_RETURN = 2 if mt5 is None else mt5.ORDER_FILLING_RETURN


class MT5TradeExecutor:
    """Handles sending order requests to MetaTrader 5 (either directly or via Execution Bridge)."""

    def __init__(self, config: dict):
        self.config = config
        self.magic = config.get("realtime_magic_number", 123456)
        self.deviation = config.get("realtime_deviation", 20)
        self.default_lot_size = config.get("realtime_default_lot_size", 0.01)
        self.risk_guard = RiskGuard(config)
        self.circuit_breaker = self.risk_guard
        # Account-wide pre-trade caps (concurrent positions + daily-loss halt).
        self.portfolio_guard = PortfolioGuard(config)
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

    def execute_signal(self, symbol: str, signal: str, live_state: Optional[Any] = None, sl: Optional[float] = None, tp: Optional[float] = None, price: Optional[float] = None) -> Optional[dict]:
        """Convert a 5-tier signal into an MT5 order action.

        Signals: Buy, Overweight, Hold, Underweight, Sell
        """
        logger.info("TradeExecutor: Evaluating signal: %s for %s", signal, symbol)

        # Drawdown circuit breaker check — feed the breaker live equity in BOTH
        # execution modes. Previously equity was only sourced in direct mode, so in
        # bridge mode (the production mode) current_equity stayed 0.0 and the daily
        # drawdown breaker was permanently disarmed on the live account.
        is_bridge = self.config.get("realtime_execution_mode", "direct") == "bridge"
        if is_bridge:
            try:
                from axonai.realtime.execution_client import send_execution_command
                acc_res = send_execution_command(self.config, {"action": "account_info"})
                if acc_res.get("success"):
                    eq = acc_res.get("equity")
                    bal = acc_res.get("balance", eq)
                    if eq is not None:
                        self.risk_guard.update_equity(
                            float(eq), float(bal if bal is not None else eq)
                        )
            except Exception as e:
                logger.error(
                    "RiskGuard: bridge equity fetch failed (%s) — breaker may be disarmed", e
                )
        else:
            mt5_inst = self._get_mt5()
            if mt5_inst and mt5_inst.terminal_info():
                acc = mt5_inst.account_info()
                if acc:
                    self.risk_guard.update_equity(acc.equity, acc.balance)

        if self.circuit_breaker.is_tripped:
            logger.warning("CIRCUIT BREAKER ACTIVE — trade rejected")
            return {"success": False, "reason": "circuit_breaker_tripped"}

        # Portfolio-level pre-trade gate (concurrent cap + daily realized-loss
        # halt). Only runs for order-placing signals so HOLD ticks stay cheap.
        if signal in ("Buy", "Overweight", "Sell", "Underweight", "BuyLimit", "SellLimit"):
            allowed, reason = self._portfolio_gate(signal, symbol)
            if not allowed:
                logger.warning("PORTFOLIO GUARD — trade rejected: %s", reason)
                return {"success": False, "reason": reason}

        if signal in ["Buy", "Overweight"]:
            return self.send_order(symbol, ORDER_TYPE_BUY, live_state, sl, tp)
        elif signal in ["Sell", "Underweight"]:
            return self.send_order(symbol, ORDER_TYPE_SELL, live_state, sl, tp)
        elif signal == "BuyLimit":
            return self.send_order(symbol, ORDER_TYPE_BUY_LIMIT, live_state, sl, tp, price=price)
        elif signal == "SellLimit":
            return self.send_order(symbol, ORDER_TYPE_SELL_LIMIT, live_state, sl, tp, price=price)
        else:
            logger.info("TradeExecutor: Signal is %s. No order action taken (HOLD).", signal)
            return None

    def _get_all_open_positions(self):
        """All open positions on the account (across symbols and magics), used for
        the portfolio-wide exposure/concurrent cap. Returns a list on success, or
        None if the query FAILED. None is distinct from [] (genuinely flat) so the
        caller can fail CLOSED on an error instead of silently bypassing the caps."""
        is_bridge = self.config.get("realtime_execution_mode", "direct") == "bridge"
        try:
            if is_bridge:
                from axonai.realtime.execution_client import send_execution_command
                res = send_execution_command(self.config, {"action": "positions_get"})
                return res.get("positions", []) if res.get("success", False) else None
            mt5_inst = self._get_mt5()
            if mt5_inst is None:
                return None
            pos = mt5_inst.positions_get()
            return None if pos is None else list(pos)  # None=error, ()=flat
        except Exception as e:
            logger.error("PortfolioGuard: position query failed (%s) — blocking trade (fail-closed)", e)
            return None

    def _portfolio_gate(self, signal: str, symbol: Optional[str] = None) -> tuple[bool, str]:
        """Run the account-wide PortfolioGuard for an order-placing signal."""
        positions = self._get_all_open_positions()
        if positions is None:
            # Can't verify account-wide exposure -> do not risk a cap bypass.
            return False, "positions_unverified (portfolio query failed)"
        try:
            realized = float(self.risk_guard.daily_pnl.get("realized_pnl", 0.0))
        except Exception:
            realized = 0.0
        direction = "BUY" if signal in ("Buy", "Overweight", "BuyLimit") else "SELL"
        return self.portfolio_guard.check(
            positions, realized, intended_direction=direction, intended_symbol=symbol
        )

    def send_order(self, symbol: str, order_type: int, live_state: Optional[Any] = None, sl: Optional[float] = None, tp: Optional[float] = None, price: Optional[float] = None) -> Optional[dict]:
        """Send a market order with dynamic SL/TP and position sizing to MT5."""
        is_bridge = self.config.get("realtime_execution_mode", "direct") == "bridge"

        # Check connection & symbol info if running directly
        if is_bridge:
            from axonai.realtime.execution_client import send_execution_command
            bid = ask = None
            if live_state:
                if hasattr(live_state, "current_bid") and live_state.current_bid:
                    bid = live_state.current_bid
                    ask = getattr(live_state, "current_ask", None)
                elif isinstance(live_state, dict) and live_state.get("bid"):
                    bid = live_state.get("bid")
                    ask = live_state.get("ask")
            # NEVER price off a hardcoded EURUSD-shaped placeholder. current_bid is
            # 0.0 until the first tick; composing a JPY (~150) or XAU (~2400) order
            # from 1.1000/1.1005 would place entry/SL/TP at nonsensical absolute
            # levels. Refuse the order instead.
            if not bid or not ask:
                logger.error(
                    "TradeExecutor: no live price for %s (current_bid unset) — order "
                    "refused to avoid placeholder pricing", symbol,
                )
                return {"success": False, "reason": "no_live_price"}
        else:
            mt5_inst = self._get_mt5()
            if mt5_inst is None or not mt5_inst.terminal_info():
                logger.error("TradeExecutor: Not connected to MT5 terminal.")
                return None

            # Refuse to route a LIVE order through the FEED (data) terminal. In direct
            # mode _get_mt5() falls back to the feed-bound MT5 singleton whenever no
            # dedicated trade terminal is initialized (get_mt5_trade() is None), which
            # would execute real orders on the Exness/data account. Production uses
            # bridge mode; this is a guardrail for a --feed-path/no-exec-path misstart.
            is_live_order = not (
                self.paper_trade
                or self.config.get("paper_trade", False)
                or self.config.get("realtime_dry_run", False)
            )
            if (
                is_live_order
                and get_mt5_trade() is None
                and not self.config.get("allow_direct_feed_execution", False)
            ):
                logger.error(
                    "TradeExecutor: direct-mode live order BLOCKED — no dedicated trade "
                    "terminal, order would route through the feed/data terminal. Launch "
                    "with bridge execution (--exec-path) or set allow_direct_feed_execution=True."
                )
                return {"success": False, "reason": "no_trade_terminal"}

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

        if price is None:
            price = ask if order_type in [ORDER_TYPE_BUY, ORDER_TYPE_BUY_LIMIT] else bid

        # Position conflict guard: enforce cap of maximum 1 open position per strategy (by magic number)
        if is_bridge:
            res = send_execution_command(self.config, {"action": "positions_get", "symbol": symbol, "magic": self.magic})
            # Fail CLOSED: a failed/timed-out query must NOT be read as "0 open
            # positions" (that would let a duplicate full-risk order through for a
            # symbol that already has one open). Skip the order until we can verify.
            if not res.get("success", False):
                logger.warning("TradeExecutor: position query failed for %s magic %d — skipping order (fail-closed).", symbol, self.magic)
                return None
            existing_count = len(res.get("positions", []))
            if existing_count >= 1:
                logger.info("TradeExecutor: Position already open for magic %d on execution bridge. Skipping new order.", self.magic)
                return None
        else:
            mt5_inst = self._get_mt5()
            # Scope by SYMBOL as well as magic. The magic number is shared across all
            # symbols, so an unfiltered positions_get() would make this a portfolio-wide
            # cap (one open EURUSD blocking a new XAUUSD entry). The bridge path already
            # scopes by symbol; mirror it here.
            existing = mt5_inst.positions_get(symbol=symbol)
            if existing:
                system_existing = [p for p in existing if p.magic == self.magic]
                if len(system_existing) >= 1:
                    logger.info("TradeExecutor: Position already open for magic %d on %s. Skipping new order.", self.magic, symbol)
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

        # Coerce to float — snapshot/getattr may yield a non-numeric (e.g. a Mock
        # in tests, or an unexpected type) which would crash the max() below.
        try:
            atr = float(atr)
        except (TypeError, ValueError):
            atr = 0.0

        # Fallback if ATR is unavailable or zero
        if atr <= 0.0:
            atr = price * 0.0015  # default to 0.15% of price
            logger.info("TradeExecutor: ATR unavailable. Using fallback value: %.5f", atr)

        # 2. Calculate ATR-based Stop Loss & PLACEHOLDER Take Profit
        # SL remains fixed (hard backstop). TP is a wide placeholder — ExitEngine drives actual exits.
        entry = price
        # Classify by membership in the BUY set: ORDER_TYPE_BUY_LIMIT must count as
        # BUY, else auto-computed SL/TP invert for a buy-limit (SL above / TP below).
        direction = "BUY" if order_type in (ORDER_TYPE_BUY, ORDER_TYPE_BUY_LIMIT) else "SELL"
        
        # Determine pip size and digits dynamically
        # Symbol-based pip/digits defaults (always valid numbers)
        if "JPY" in symbol.upper() or "XAU" in symbol.upper():
            pip, digits = 0.01, 3
        else:
            pip, digits = 0.0001, 5
        # Prefer broker-reported precision when available AND numeric (guards against
        # a bad/non-numeric point, which would otherwise corrupt SL/TP/spread math).
        if not is_bridge and mt5_inst:
            s_info = mt5_inst.symbol_info(symbol)
            if s_info is not None:
                try:
                    p = float(s_info.point) * 10
                    if p > 0:
                        pip, digits = p, int(s_info.digits)
                except (TypeError, ValueError):
                    pass

        # Use explicit SL/TP if provided
        if sl is not None and tp is not None:
            sl_distance = abs(entry - sl)
            tp_distance = abs(entry - tp)
        else:
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

        if sl is None or tp is None:
            sl = entry - sl_distance if direction == "BUY" else entry + sl_distance
            tp = entry + tp_distance if direction == "BUY" else entry - tp_distance

        # Format price to correct number of digits
        sl = round(sl, digits)
        tp = round(tp, digits)
        price = round(price, digits)

        # 3. Position Sizing — PURE 1% RISK HARD-LOCK
        #    A single trade never risks more than realtime_risk_pct (default 1%)
        #    of account equity. Lot floats with equity and stop distance:
        #        lot = (equity * risk_pct) / (sl_pips * pip_value_per_lot)
        #    Currency-consistent: pip_value_per_lot comes from the broker's
        #    trade_tick_value, which is denominated in the ACCOUNT currency —
        #    the same units as equity — so no USD assumption is made and no flat
        #    USD cap is needed (the % itself is the hard cap). Result is clamped
        #    to the broker's volume_min/volume_step/volume_max plus a config
        #    backstop ceiling (realtime_max_lot) to contain pip-miscalc blow-ups.
        #    - dry-run: fixed 1.00 lot (sandbox)
        #    - realtime_dynamic_sizing == False: fixed realtime_default_lot_size
        is_mock_env = self.config.get("realtime_dry_run", False)

        if is_mock_env:
            lot = 1.00
            logger.info("TradeExecutor: Dryrun active. Using fixed lot size: 1.00")
        elif self.config.get("realtime_dynamic_sizing", True):
            mt5_inst = None
            equity = None
            sym_res = None
            if is_bridge:
                res = send_execution_command(self.config, {"action": "account_info"})
                if res.get("success") and res.get("equity"):
                    equity = float(res["equity"])
                sym_res = send_execution_command(self.config, {"action": "symbol_info", "symbol": symbol})
            else:
                mt5_inst = self._get_mt5()
                acc = mt5_inst.account_info() if mt5_inst else None
                if acc and getattr(acc, "equity", 0):
                    equity = float(acc.equity)

            if equity is None or equity <= 0:
                # Could not read equity (bridge down / MT5 not connected). Refuse to
                # guess a large account — fall back to the smallest safe lot.
                fallback_lot = max(0.01, float(self.config.get("realtime_default_lot_size", 0.01)))
                logger.warning(
                    "TradeExecutor: equity unavailable for 1%% sizing — using fallback lot %.2f",
                    fallback_lot,
                )
                lot = fallback_lot
            else:
                risk_pct = float(self.config.get("realtime_risk_pct", 0.01))
                risk_amount = equity * risk_pct          # HARD 1% lock, account currency
                max_risk_usd = float(self.config.get("realtime_max_risk_usd", 100.0))
                risk_amount = min(max_risk_usd, risk_amount)

                sl_pips = sl_distance / pip
                if sl_pips < 1.0:
                    logger.warning("TradeExecutor: SL distance too small (%.4f pips) - entry skipped", sl_pips)
                    return {"success": False, "reason": "sl_too_small", "sl_pips": round(sl_pips, 4)}

                # Broker contract constraints (account-currency tick value)
                pip_value_per_lot = 10.0  # fallback
                tick_value = tick_size = 0.0
                vol_min, vol_step, vol_max = 0.01, 0.01, None
                if is_bridge:
                    if sym_res and sym_res.get("success"):
                        tick_value = sym_res.get("trade_tick_value", 0.0)
                        tick_size = sym_res.get("trade_tick_size", 0.0)
                        vol_min = sym_res.get("volume_min", vol_min) or vol_min
                        vol_step = sym_res.get("volume_step", vol_step) or vol_step
                        vol_max = sym_res.get("volume_max", vol_max)
                else:
                    s_info = mt5_inst.symbol_info(symbol) if mt5_inst else None
                    if s_info:
                        tick_value = s_info.trade_tick_value
                        tick_size = s_info.trade_tick_size
                        vol_min = getattr(s_info, "volume_min", vol_min) or vol_min
                        vol_step = getattr(s_info, "volume_step", vol_step) or vol_step
                        vol_max = getattr(s_info, "volume_max", vol_max)

                # Coerce all broker-reported numerics defensively (a bad/non-numeric
                # value from the bridge or a Mock must never crash sizing math).
                def _num(v, default):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return default
                tick_value = _num(tick_value, 0.0)
                tick_size = _num(tick_size, 0.0)
                vol_min = _num(vol_min, 0.01) or 0.01
                vol_step = _num(vol_step, 0.01) or 0.01
                vol_max = _num(vol_max, 0.0) or None

                is_gold_symbol = (pip == 0.01 and price > 1000.0)
                if is_gold_symbol:
                    pip_value_per_lot = 1.0
                elif tick_value > 0 and tick_size > 0:
                    pip_value_per_lot = (pip / tick_size) * tick_value
                else:
                    pip_value_per_lot = 10.0  # standard fallback for currency pairs

                raw_lot = risk_amount / max(sl_pips * pip_value_per_lot, 1e-6)

                # Hard ceiling = min(broker volume_max, config backstop)
                # Gold gets a tighter ceiling (default 1.00) because $1 pip-value
                # per lot means a 5.00 lot risks ~$500/pip — catastrophic.
                is_gold_symbol = (pip == 0.01 and price > 1000.0)
                if is_gold_symbol:
                    ceiling = float(self.config.get("realtime_max_lot_gold", 1.0))
                else:
                    ceiling = float(self.config.get("realtime_max_lot", 2.0))
                if vol_max and vol_max > 0:
                    ceiling = min(ceiling, float(vol_max))
                if raw_lot > ceiling:
                    logger.warning(
                        "TradeExecutor: 1%% lot %.4f exceeds ceiling %.2f (check SL/pip-value) — clamping",
                        raw_lot, ceiling,
                    )

                # Clamp to [vol_min, ceiling] then floor to broker volume_step
                step = vol_step if vol_step > 0 else 0.01
                lot = max(vol_min, min(raw_lot, ceiling))
                # Floor to broker volume_step; +1e-9 absorbs float error so an exact
                # step multiple (e.g. 0.29/0.01) is not truncated by one extra step.
                lot = max(vol_min, round((int(lot / step + 1e-9)) * step, 2))

                logger.info(
                    "TradeExecutor: 1%%-lock sizing | equity: %.2f | risk: %.2f | "
                    "SL pips: %.2f | pip val: %.2f | raw lot: %.4f | final lot: %.2f "
                    "(min=%.2f step=%.2f max=%s)",
                    equity, risk_amount, sl_pips, pip_value_per_lot, raw_lot, lot,
                    vol_min, step, vol_max,
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
                    f"Trade Executed (Bridge): {symbol} | Type: {'BUY' if order_type in (ORDER_TYPE_BUY, ORDER_TYPE_BUY_LIMIT) else 'SELL'} "
                    f"| Volume: {lot:.2f} | Price: {price:.5f} | SL: {sl:.5f} | TP: {tp:.5f} | Ticket: {result.get('order')}",
                    self.config
                )
                return result
            else:
                logger.error("TradeExecutor: Execution bridge order failed. Reason: %s", result.get("reason"))
                send_alert(f"Trade FAILED (Bridge): {symbol} | Type: {'BUY' if order_type in (ORDER_TYPE_BUY, ORDER_TYPE_BUY_LIMIT) else 'SELL'} | Reason: {result.get('reason')}", self.config)
                return None
        else:
            # Prepare request for direct MT5
            is_limit = order_type in [ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT]
            req_action = TRADE_ACTION_PENDING if is_limit else TRADE_ACTION_DEAL
            req_filling = ORDER_FILLING_RETURN if is_limit else ORDER_FILLING_FOK
            request = {
                "action": req_action,
                "symbol": symbol,
                "volume": lot,
                "type": order_type,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": self.deviation,
                "magic": self.magic,
                "comment": f"AxonAI {order_type} execution",
                "type_time": ORDER_TIME_GTC,
                "type_filling": req_filling,
            }

            logger.info("TradeExecutor: Sending order request with SL/TP: %s", request)
            mt5_inst = self._get_mt5()
            result = mt5_inst.order_send(request) if mt5_inst else None
            if result is None:
                logger.error("TradeExecutor: order_send returned None")
                return None

            # Track PnL if trade fails or succeeds. A partial fill (DONE_PARTIAL)
            # opened a real position and must be treated as success, not failure.
            if result.retcode not in (TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL):
                logger.error("TradeExecutor: Order failed. Retcode: %d, Comment: %s",
                             result.retcode, result.comment)
                
                send_alert(
                    f"Trade FAILED: {symbol} | Type: {'BUY' if order_type in (ORDER_TYPE_BUY, ORDER_TYPE_BUY_LIMIT) else 'SELL'} "
                    f"| Retcode: {result.retcode} | Comment: {result.comment}",
                    self.config
                )
                
                # Try with another filling type if FOK fails (e.g. IOC)
                if result.retcode in [mt5.TRADE_RETCODE_INVALID_FILL, mt5.TRADE_RETCODE_LIMIT_VOLUME]:
                    logger.info("TradeExecutor: Retrying with ORDER_FILLING_IOC...")
                    request["type_filling"] = mt5.ORDER_FILLING_IOC
                    result = mt5.order_send(request)
                    if result and result.retcode in (TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL):
                        logger.info("TradeExecutor: Order successful on retry! Ticket: %d", result.order)
                        send_alert(
                            f"Trade Executed on Retry: {symbol} | Type: {'BUY' if order_type in (ORDER_TYPE_BUY, ORDER_TYPE_BUY_LIMIT) else 'SELL'} "
                            f"| Volume: {lot:.2f} | Price: {price:.5f} | SL: {sl:.5f} | TP: {tp:.5f} | Ticket: {result.order}",
                            self.config
                        )
                        return self._result_to_dict(result, sl)
                return self._result_to_dict(result, sl)

            logger.info("TradeExecutor: Order executed successfully! Ticket: %d", result.order)
            send_alert(
                f"Trade Executed: {symbol} | Type: {'BUY' if order_type in (ORDER_TYPE_BUY, ORDER_TYPE_BUY_LIMIT) else 'SELL'} "
                f"| Volume: {lot:.2f} | Price: {price:.5f} | SL: {sl:.5f} | TP: {tp:.5f} | Ticket: {result.order}",
                self.config
            )
            return self._result_to_dict(result, sl)

    def _simulate_fill(self, symbol: str, order_type: int, lot: float,
                       price: float, sl: float, tp: float) -> dict:
        """Simulate an instant fill at the requested price (paper-trade mode)."""
        self._paper_ticket_seq += 1
        ticket = 900_000_000 + self._paper_ticket_seq
        side = "BUY" if order_type in (ORDER_TYPE_BUY, ORDER_TYPE_BUY_LIMIT) else "SELL"
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
            "success": result.retcode in (TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL),
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

    def cancel_pending_order(self, ticket: int) -> bool:
        """Cancel an MT5 pending limit/stop order."""
        logger.info("TradeExecutor: Cancelling pending order ticket: %d", ticket)
        if ticket >= 900_000_000:
            logger.info("TradeExecutor[PAPER]: Cancelled simulated pending order: %d", ticket)
            return True

        is_bridge = self.config.get("realtime_execution_mode", "direct") == "bridge"
        if is_bridge:
            from axonai.realtime.execution_client import send_execution_command
            res = send_execution_command(self.config, {"action": "order_cancel", "order": ticket})
            return res.get("success", False)
        else:
            mt5_inst = self._get_mt5()
            if not mt5_inst:
                return False
            # TRADE_ACTION_REMOVE = 8
            request = {
                "action": 8,
                "order": ticket
            }
            result = mt5_inst.order_send(request)
            if result is None:
                logger.error("TradeExecutor: cancel_pending_order order_send returned None")
                return False
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                logger.error("TradeExecutor: Cancel failed. Retcode: %d, Comment: %s", result.retcode, result.comment)
                return False
            logger.info("TradeExecutor: Cancelled pending order successfully: %d", ticket)
            return True
