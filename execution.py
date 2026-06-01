"""Execution engine for filtering and dispatching trades via MT5."""

import logging
import time
from dataclasses import dataclass
from typing import Optional, List, Dict
import MetaTrader5 as mt5

from llm_bridge import LLMDecision
from market_context import MarketContext

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class TradeRecord:
    """Historical log of trade attempts and executions."""
    symbol: str
    direction: str
    lot_size: float
    entry_price: float
    sl_price: float
    tp_price: float
    timestamp_ms: int
    llm_confidence: float
    market_state: str
    session: str
    executed: bool
    rejection_reason: Optional[str]

class ExecutionEngine:
    """Validates trade setups, calculates dynamic risk sizes, and manages broker execution."""
    def __init__(self, dry_run: bool = True, debug: bool = False, symbols: Optional[List[str]] = None):
        self.dry_run = dry_run
        self.debug = debug
        self.trades: List[TradeRecord] = []
        self._pip_value_cache: Dict[str, float] = {}
        
        # Pre-cache pip values per symbol dynamically on init (not on every trade)
        target_symbols = symbols or ["EURUSD", "GBPUSD", "EURUSD=X", "GBPUSD=X"]
        if not self.dry_run:
            try:
                mt5.initialize()
            except Exception:
                pass
        for sym in target_symbols:
            self._get_pip_value(sym)

    def evaluate(self, decision: LLMDecision, context: MarketContext) -> Optional[TradeRecord]:
        """Evaluates entry filters and executes the trade if they all pass."""
        symbol = context.symbol
        
        # 1. Entry Filters
        if decision.action == "wait":
            return None

        if decision.confidence < 0.70:
            return self._reject(decision, context, f"LLM Confidence too low: {decision.confidence:.2f}")

        if not context.spread_safe:
            return self._reject(decision, context, f"Spread unsafe: {context.spread_pips:.2f}")

        allowed_sessions = {"london", "new_york", "london_new_york_overlap"}
        if context.session not in allowed_sessions:
            return self._reject(decision, context, f"Invalid session for trading: {context.session}")

        if self._has_open_position(symbol):
            return self._reject(decision, context, f"Already have open position in {symbol}")

        # Stop loss constraints
        stop_pips = max(10, min(30, decision.max_risk_pips))

        # 2. Position Sizing
        # Fetch account balance with robust guard
        balance = 10000.0  # Default fallback balance
        use_fallback = False

        if not self.dry_run:
            try:
                acc_info = mt5.account_info()
                if acc_info is not None and getattr(acc_info, "balance", None) is not None and acc_info.balance > 0.0:
                    balance = acc_info.balance
                else:
                    use_fallback = True
                    logger.warning("MT5 account_info is None or balance is invalid/zero. Falling back to minimum safe lot size 0.01.")
            except Exception as e:
                use_fallback = True
                logger.error("Error fetching MT5 account info: %s. Falling back to minimum safe lot size 0.01.", e)

        if use_fallback:
            lot_size = 0.01
        else:
            risk_amount = balance * 0.01
            # Dynamic pip value from MT5 broker data (cached per symbol)
            pip_value = self._get_pip_value(symbol)
            lot_size = risk_amount / (stop_pips * pip_value)
            lot_size = float(int(lot_size * 100)) / 100.0  # Round down to 2 decimal places
            lot_size = min(0.10, max(0.01, lot_size))       # Hard cap at 0.10 lots, min 0.01

        # 3. Calculate SL/TP prices
        tick_info = mt5.symbol_info_tick(symbol) if not self.dry_run else None
        
        # Mock prices for dry run
        if self.dry_run or tick_info is None:
            entry_price = context.context_snapshot.mid if hasattr(context, 'context_snapshot') else 1.1600
            if decision.action == "long":
                sl_price = entry_price - (stop_pips * 0.0001)
                tp_price = entry_price + (stop_pips * 2.0 * 0.0001)
            else:
                sl_price = entry_price + (stop_pips * 0.0001)
                tp_price = entry_price - (stop_pips * 2.0 * 0.0001)
        else:
            entry_price = float(tick_info.ask) if decision.action == "long" else float(tick_info.bid)
            if decision.action == "long":
                sl_price = entry_price - (stop_pips * 0.0001)
                tp_price = entry_price + (stop_pips * 2.0 * 0.0001)
            else:
                sl_price = entry_price + (stop_pips * 0.0001)
                tp_price = entry_price - (stop_pips * 2.0 * 0.0001)

        # Dry run execution simulation
        if self.dry_run:
            record = TradeRecord(
                symbol=symbol,
                direction=decision.action,
                lot_size=lot_size,
                entry_price=entry_price,
                sl_price=sl_price,
                tp_price=tp_price,
                timestamp_ms=int(time.time() * 1000),
                llm_confidence=decision.confidence,
                market_state=context.current_state,
                session=context.session,
                executed=True,
                rejection_reason=None
            )
            self.trades.append(record)
            if self.debug:
                print(f"[Execution Paper] Simulated trade executed: {record}")
            return record

        # 4. MT5 Real Execution
        order_type = mt5.ORDER_TYPE_BUY if decision.action == "long" else mt5.ORDER_TYPE_SELL
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot_size,
            "type": order_type,
            "price": entry_price,
            "sl": sl_price,
            "tp": tp_price,
            "deviation": 10,
            "magic": 20260528,
            "comment": "Antigravity Quant",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        # Send order with requote retry once
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            # Check for requote
            if result.retcode in [mt5.TRADE_RETCODE_REQUOTE, mt5.TRADE_RETCODE_PRICE_OFF]:
                logger.warning("Requote encountered. Retrying trade entry once...")
                time.sleep(0.5)
                # Re-fetch price
                new_tick = mt5.symbol_info_tick(symbol)
                if new_tick:
                    entry_price = float(new_tick.ask) if decision.action == "long" else float(new_tick.bid)
                    request["price"] = entry_price
                    result = mt5.order_send(request)

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            record = TradeRecord(
                symbol=symbol,
                direction=decision.action,
                lot_size=lot_size,
                entry_price=entry_price,
                sl_price=sl_price,
                tp_price=tp_price,
                timestamp_ms=int(time.time() * 1000),
                llm_confidence=decision.confidence,
                market_state=context.current_state,
                session=context.session,
                executed=True,
                rejection_reason=None
            )
            logger.info("Trade successfully executed on MT5: %s", record)
        else:
            record = TradeRecord(
                symbol=symbol,
                direction=decision.action,
                lot_size=lot_size,
                entry_price=entry_price,
                sl_price=sl_price,
                tp_price=tp_price,
                timestamp_ms=int(time.time() * 1000),
                llm_confidence=decision.confidence,
                market_state=context.current_state,
                session=context.session,
                executed=False,
                rejection_reason=f"MT5 order_send rejected: {result.retcode} - {result.comment}"
            )
            logger.error("Trade failed execution on MT5: %s", record)

        self.trades.append(record)
        return record

    def _get_pip_value(self, symbol: str) -> float:
        """Fetch pip value from cache or fallback to $10/pip using dynamic MT5 lookup."""
        if symbol in self._pip_value_cache:
            return self._pip_value_cache[symbol]
        
        pip_val = 10.0
        try:
            if not self.dry_run:
                symbol_info = mt5.symbol_info(symbol)
                pip_val = symbol_info.trade_tick_value if symbol_info else 10.0
        except Exception as e:
            logger.warning("Failed to fetch pip value for %s: %s. Using fallback $10/pip.", symbol, e)
            
        self._pip_value_cache[symbol] = pip_val
        return pip_val

    def _reject(self, decision: LLMDecision, context: MarketContext, reason: str) -> TradeRecord:
        """Helper to log trade rejection reasons."""
        record = TradeRecord(
            symbol=context.symbol,
            direction=decision.action,
            lot_size=0.0,
            entry_price=0.0,
            sl_price=0.0,
            tp_price=0.0,
            timestamp_ms=int(time.time() * 1000),
            llm_confidence=decision.confidence,
            market_state=context.current_state,
            session=context.session,
            executed=False,
            rejection_reason=reason
        )
        self.trades.append(record)
        if self.debug:
            print(f"[Execution Filtered] Rejection: {reason}")
        return record

    def _has_open_position(self, symbol: str) -> bool:
        """Checks if there is already an open position for this symbol."""
        if self.dry_run:
            # In paper/dry run mode, simulate no open positions to allow continuous testing
            return False
        
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            return False
        return len(positions) > 0
