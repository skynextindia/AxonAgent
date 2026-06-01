import pytest
from axonai.realtime.backtester import BacktestEngine

@pytest.mark.unit
class TestBacktestEngine:
    def test_backtest_run_success(self):
        # Run a 2-day synthetic backtest
        engine = BacktestEngine(ticker="EURUSD=X", days=2)
        report = engine.run()
        
        # Verify structure
        assert report is not None
        assert report["ticker"] == "EURUSD=X"
        assert report["days"] == 2
        assert "wins" in report
        assert "losses" in report
        assert "win_rate_percent" in report
        assert "net_profit_pips" in report
        assert "trades" in report
        assert "events" in report
        
        # Verify markdown serialization doesn't crash
        md = engine.generate_markdown_report(report)
        assert len(md) > 100
        assert "# AxonAI Backtesting Performance Report" in md
