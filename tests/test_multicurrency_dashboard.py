import pytest
import time
from fastapi.testclient import TestClient
from axonai.realtime.api_server import DashboardServer

class MockDaemon:
    def __init__(self, symbol):
        self.yf_symbol = symbol
        self.mt5_symbol = symbol
        self.config = {"symbol": symbol}
        
    def _get_candles_payload(self, tf):
        return {"type": "candles", "timeframe": tf, "symbol": self.mt5_symbol, "data": []}

def test_dashboard_server_multicurrency_registration():
    server = DashboardServer(host="127.0.0.1", port=8000)
    
    daemon1 = MockDaemon("EURUSD")
    daemon2 = MockDaemon("XAUUSD")
    
    server.register_daemon("EURUSD", daemon1)
    server.register_daemon("XAUUSD", daemon2)
    
    assert "EURUSD" in server.active_symbols
    assert "XAUUSD" in server.active_symbols
    
    assert "EURUSD" in server.symbol_history
    assert "XAUUSD" in server.symbol_history
    
    # Legacy daemon points to the first registered one
    assert server.daemon == daemon1
    
    # Test caching a tick correctly routes to symbol history
    server.broadcast({
        "type": "tick",
        "symbol": "XAUUSD",
        "bid": 2500.5,
        "ask": 2500.8
    })
    
    assert server.symbol_history["XAUUSD"]["tick"]["bid"] == 2500.5
    assert server.symbol_history["EURUSD"]["tick"] is None

def test_dashboard_server_global_broadcast():
    server = DashboardServer(host="127.0.0.1", port=8000)
    server.register_daemon("EURUSD", MockDaemon("EURUSD"))
    
    # Send account broadcast (global, no symbol)
    server.broadcast({
        "type": "account",
        "balance": 10000.0,
        "equity": 10050.0
    })
    
    # Should land in global history, not symbol history
    assert server.history["account"]["balance"] == 10000.0
    assert server.symbol_history.get("EURUSD", {}).get("account") is None
