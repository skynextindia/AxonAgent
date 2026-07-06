"""
MT5 Order Bridge for dual-terminal architecture.

Maintains data feed on Exness (via main MT5 module).
Sends orders through separate subprocess connection to Default MT5.

This solves the MT5 module limitation: only ONE active connection per module instance.
"""

import logging
import subprocess
import json
import sys
from typing import Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

_bridge_process = None
_bridge_script = None


def _get_bridge_script() -> str:
    """Return the inline bridge script that runs in subprocess."""
    return '''
import sys
import json
import MetaTrader5 as mt5

_connected_path = None

def ensure_connected(mt5_path):
    """Ensure we're connected to the right terminal."""
    global _connected_path
    if _connected_path == mt5_path and mt5.terminal_info():
        return True

    try:
        mt5.shutdown()
    except:
        pass

    if mt5.initialize(path=mt5_path):
        _connected_path = mt5_path
        return True
    return False

def handle_order(mt5_path, request_dict):
    """Send order via Default MT5 terminal."""
    try:
        if not ensure_connected(mt5_path):
            return {"success": False, "error": "Cannot connect to MT5"}

        request = mt5.TradeRequest()
        for key, val in request_dict.items():
            if hasattr(request, key):
                setattr(request, key, val)

        result = mt5.order_send(request)

        if result:
            return {
                "success": True,
                "retcode": result.retcode,
                "order": result.order,
                "deal": result.deal,
                "volume": result.volume,
                "price": result.price,
                "comment": result.comment,
            }
        else:
            return {"success": False, "error": str(mt5.last_error())}
    except Exception as e:
        return {"success": False, "error": str(e)}

def handle_account_info(mt5_path):
    """Get account info from Default MT5 terminal."""
    try:
        if not ensure_connected(mt5_path):
            return {"success": False, "error": "Cannot connect to MT5"}

        acc = mt5.account_info()
        if not acc:
            return {"success": False, "error": "account_info() returned None"}

        return {
            "success": True,
            "login": acc.login,
            "balance": acc.balance,
            "equity": acc.equity,
            "profit": acc.profit,
            "margin": acc.margin,
            "margin_free": acc.margin_free,
            "margin_level": getattr(acc, "margin_level", 0.0),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def handle_positions_get(mt5_path, symbol):
    """Get positions from Default MT5 terminal."""
    try:
        if not ensure_connected(mt5_path):
            return {"success": False, "error": "Cannot connect to MT5"}

        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return {"success": True, "positions": []}

        pos_list = []
        for p in positions:
            pos_list.append({
                "ticket": p.ticket,
                "symbol": p.symbol,
                "type": 0 if p.type == mt5.POSITION_TYPE_BUY else 1,
                "volume": p.volume,
                "price_open": p.price_open,
                "price_current": p.price_current,
                "sl": p.sl,
                "tp": p.tp,
                "profit": p.profit,
            })

        return {"success": True, "positions": pos_list}
    except Exception as e:
        return {"success": False, "error": str(e)}

def handle_orders_get(mt5_path, symbol):
    """Get pending orders from Default MT5 terminal."""
    try:
        if not ensure_connected(mt5_path):
            return {"success": False, "error": "Cannot connect to MT5"}

        orders = mt5.orders_get(symbol=symbol)
        if not orders:
            return {"success": True, "orders": []}

        ord_list = []
        for o in orders:
            ord_type = "buy_limit" if o.type == 2 else "sell_limit" if o.type == 3 else "buy_stop" if o.type == 4 else "sell_stop" if o.type == 5 else "other"
            ord_list.append({
                "ticket": o.ticket,
                "symbol": o.symbol,
                "type": ord_type,
                "volume_initial": o.volume_initial,
                "price_open": o.price_open,
                "price_current": o.price_current,
                "sl": o.sl,
                "tp": o.tp,
            })

        return {"success": True, "orders": ord_list}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break

            data = json.loads(line)
            action = data.get("action", "order")
            mt5_path = data.get("mt5_path")

            if action == "order":
                result = handle_order(mt5_path, data.get("request", {}))
            elif action == "account_info":
                result = handle_account_info(mt5_path)
            elif action == "positions_get":
                result = handle_positions_get(mt5_path, data.get("symbol", ""))
            elif action == "orders_get":
                result = handle_orders_get(mt5_path, data.get("symbol", ""))
            else:
                result = {"success": False, "error": "Unknown action"}

            print(json.dumps(result))
            sys.stdout.flush()
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}))
            sys.stdout.flush()
'''


def start_bridge(mt5_trade_terminal_path: str) -> bool:
    """Start the order bridge subprocess."""
    global _bridge_process

    if _bridge_process is not None:
        return True

    try:
        _bridge_process = subprocess.Popen(
            [sys.executable, "-c", _get_bridge_script()],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        logger.info("MT5 order bridge started (subprocess)")
        return True
    except Exception as e:
        logger.error(f"Failed to start MT5 order bridge: {e}")
        _bridge_process = None
        return False


def _send_bridge_command(
    mt5_trade_terminal_path: str,
    command: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Send a command through the bridge subprocess."""
    global _bridge_process

    if _bridge_process is None:
        if not start_bridge(mt5_trade_terminal_path):
            return None

    try:
        command["mt5_path"] = mt5_trade_terminal_path
        _bridge_process.stdin.write(json.dumps(command) + "\n")
        _bridge_process.stdin.flush()

        result_line = _bridge_process.stdout.readline()
        if result_line:
            return json.loads(result_line)
        else:
            return {"success": False, "error": "Bridge process died"}
    except Exception as e:
        logger.error(f"Bridge command failed: {e}")
        _bridge_process = None
        return None


def send_order_via_bridge(
    mt5_trade_terminal_path: str,
    request: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Send an order through the bridge subprocess.

    Args:
        mt5_trade_terminal_path: Path to default MT5 terminal
        request: MT5 trade request dict

    Returns:
        Order result dict or None on error
    """
    command = {
        "action": "order",
        "request": request,
    }
    return _send_bridge_command(mt5_trade_terminal_path, command)


def get_account_info_via_bridge(mt5_trade_terminal_path: str) -> Optional[Dict[str, Any]]:
    """Get account info from Default MT5 via bridge."""
    command = {"action": "account_info"}
    return _send_bridge_command(mt5_trade_terminal_path, command)


def get_positions_via_bridge(
    mt5_trade_terminal_path: str,
    symbol: str
) -> Optional[Dict[str, Any]]:
    """Get positions from Default MT5 via bridge."""
    command = {
        "action": "positions_get",
        "symbol": symbol,
    }
    return _send_bridge_command(mt5_trade_terminal_path, command)


def get_orders_via_bridge(
    mt5_trade_terminal_path: str,
    symbol: str
) -> Optional[Dict[str, Any]]:
    """Get pending orders from Default MT5 via bridge."""
    command = {
        "action": "orders_get",
        "symbol": symbol,
    }
    return _send_bridge_command(mt5_trade_terminal_path, command)


def stop_bridge():
    """Shut down the order bridge subprocess."""
    global _bridge_process
    if _bridge_process is not None:
        try:
            _bridge_process.terminate()
            _bridge_process.wait(timeout=2)
        except Exception as e:
            logger.warning(f"Error stopping bridge: {e}")
            try:
                _bridge_process.kill()
            except:
                pass
        _bridge_process = None
        logger.info("MT5 order bridge stopped")
