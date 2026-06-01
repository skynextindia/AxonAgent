from axonai.graph.trading_graph import AxonAIGraph
from axonai.default_config import DEFAULT_CONFIG

# Configure data vendors to route to mt5 for price and technical indicators
config = DEFAULT_CONFIG.copy()
config["data_vendors"] = {
    "core_stock_apis": "mt5",
    "technical_indicators": "mt5",
    "fundamental_data": "yfinance",
    "news_data": "yfinance",
}

# Initialize with custom config
ta = AxonAIGraph(debug=True, config=config)

# forward propagate with EURUSD=X as a forex asset
_, decision = ta.propagate("EURUSD=X", "2024-05-10", asset_type="forex")
print(decision)
