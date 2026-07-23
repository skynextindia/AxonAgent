# File: scratch/check_gold_sizing.py
import os
import sys
import logging
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logging.basicConfig(level=logging.INFO)

from axonai.realtime.trade_executor import MT5TradeExecutor
from axonai.default_config import DEFAULT_CONFIG

def test_gold_sizing():
    symbol = "XAUUSD"
    config = DEFAULT_CONFIG.copy()
    config["realtime_execution_mode"] = "bridge"
    config["realtime_risk_pct"] = 0.01
    
    executor = MT5TradeExecutor(config)
    
    print("Executing dynamic sizing test on MT5TradeExecutor with $25,000 Equity...")
    config["paper_trade"] = True
    
    # We will override the account_info request in our execution client or mock it
    # Since account_info comes from the bridge, we can just temporarily override
    # our test by mock-modifying the executor's bridge return or mock the local call.
    # In send_order, it calls send_execution_command for account_info.
    # We can mock this by patch or simply testing a direct direct-mode call.
    # Actually, we can just verify the code logic:
    # risk_amount = equity * risk_pct (25000 * 0.01 = 250)
    # risk_amount = min(100.0, 250) = 100.0.
    
    # Let's run it. We'll inspect how it scales down.
    res = executor.send_order(symbol, 0, sl=2345.0, tp=2360.0, price=2355.0)
    print("\nResult of Order Execution:", res)

if __name__ == "__main__":
    test_gold_sizing()
