#!/usr/bin/env python3
"""
Force a test entry to demonstrate order execution + lifecycle tracking.
Usage: python test_live_entry.py
"""

import sys
import logging
import time
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5
except ImportError:
    logger.error("MetaTrader5 not installed")
    sys.exit(1)

from axonai.dataflows.mt5_data import mt5_initialize_trade, get_mt5_trade, _to_mt5_symbol
from axonai.realtime.trade_executor import MT5TradeExecutor
from axonai.default_config import DEFAULT_CONFIG

def test_live_entry():
    """
    1. Initialize MT5 (trade terminal)
    2. Get current price
    3. Calculate SL/TP
    4. Send BUY order
    5. Show order details
    """

    print("\n" + "="*70)
    print("LIVE TRADE EXECUTION TEST")
    print("="*70 + "\n")

    # 1. Initialize trade terminal
    print("[1] Initializing trade terminal...")
    trade_path = DEFAULT_CONFIG.get("mt5_trade_terminal_path")
    if not mt5_initialize_trade(trade_path):
        logger.error("Failed to initialize MT5 trade terminal at: %s", trade_path)
        return False
    print("    [OK] Trade terminal connected\n")

    # 2. Get MT5 instance and current price
    print("[2] Fetching current market data...")
    mt5_inst = get_mt5_trade()
    if not mt5_inst:
        logger.error("MT5 trade instance not available")
        return False

    # Try different symbol formats
    symbol = None
    for sym_try in ["EURUSD", "EURUSDm", "EURUSD.i", "EURSD"]:
        tick = mt5_inst.symbol_info_tick(sym_try)
        if tick:
            symbol = sym_try
            break

    if not symbol:
        logger.error("EURUSD symbol not found in MT5. Available symbols required in Market Watch.")
        print("\n[ERROR] EURUSD not in default MT5 Market Watch.")
        print("        Add EURUSD to Market Watch in MT5 terminal and retry.\n")
        return False

    bid = tick.bid
    ask = tick.ask
    mid = (bid + ask) / 2
    print(f"    Symbol: {symbol}")
    print(f"    BID: {bid:.5f}")
    print(f"    ASK: {ask:.5f}")
    print(f"    Spread: {(ask - bid) / 0.0001:.1f} pips\n")

    # 3. Calculate SL/TP
    print("[3] Calculating order parameters...")
    sl_pips = 20  # 20 pips SL
    tp_pips = 40  # 40 pips TP
    pip_unit = 0.0001

    entry_price = ask  # BUY at ask
    sl_price = entry_price - (sl_pips * pip_unit)
    tp_price = entry_price + (tp_pips * pip_unit)
    lot_size = 0.01

    print(f"    Entry Price: {entry_price:.5f}")
    print(f"    Stop Loss: {sl_price:.5f} ({sl_pips} pips below entry)")
    print(f"    Take Profit: {tp_price:.5f} ({tp_pips} pips above entry)")
    print(f"    Lot Size: {lot_size}\n")

    # 4. Place BUY order
    print("[4] Sending BUY order to MT5...")

    request = {
        "action": mt5_inst.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot_size,
        "type": mt5_inst.ORDER_TYPE_BUY,
        "price": entry_price,
        "sl": sl_price,
        "tp": tp_price,
        "deviation": 100,
        "magic": 123456,
        "comment": "AXON.AI Test",
    }

    result = mt5_inst.order_send(request)
    if not result:
        logger.error(f"Order send failed: {mt5_inst.last_error()}")
        return False

    if result.retcode != mt5_inst.TRADE_RETCODE_DONE:
        logger.error(f"Order failed with code: {result.retcode}")
        print(f"    [ERROR] Code: {result.retcode}")
        return False

    print(f"    [OK] Order ACCEPTED")
    print(f"    Retcode: {result.retcode}")
    print(f"    Result: {result}\n")

    # 5. Show filled position
    print("[5] Order execution result:")
    print(f"    Ticket: #{result.order}")
    print(f"    Status: {result.comment}")
    print(f"    Entry Price: {result.price:.5f}\n")

    # 6. Verify position opened
    time.sleep(1)
    positions = mt5_inst.positions_get(symbol=symbol)
    if positions:
        pos = positions[0]
        print("[6] Position confirmed in MT5:")
        print(f"    Ticket: #{pos.ticket}")
        print(f"    Direction: {'BUY' if pos.type == 0 else 'SELL'}")
        print(f"    Volume: {pos.volume}")
        print(f"    Entry: {pos.price_open:.5f}")
        print(f"    Current Price: {pos.price_current:.5f}")
        print(f"    Current P&L: {pos.profit:.2f}")
        print(f"    SL: {pos.sl:.5f}")
        print(f"    TP: {pos.tp:.5f}\n")

    print("="*70)
    print("[SUCCESS] ORDER EXECUTED SUCCESSFULLY")
    print("="*70)
    print("\n[WATCH] Dashboard:")
    print("   [1] TRADE_STATE panel appears with lifecycle tracking")
    print("   [2] PHASE transitions: ENTRY -> EXPANSION -> ...")
    print("   [3] HEALTH score updates (thesis/displacement)")
    print("   [4] MFE/MAE tracks max profit/loss")
    print("   [5] ExitEngine monitors exit conditions every 100ms")
    print("   [6] Position closes when exit condition met\n")

    return True

if __name__ == "__main__":
    success = test_live_entry()
    sys.exit(0 if success else 1)
