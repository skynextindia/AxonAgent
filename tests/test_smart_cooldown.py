"""Test smart dynamic cooldown logic.

Smart cooldown adjusts based on trade outcome:
- No active trade: 60 sec (fast recovery)
- Winning trade (+2+ pips): 300 sec (protect profit)
- Losing trade (-3+ pips): 60 sec (quick recovery)
- Breakeven/small loss: 120 sec (neutral cooldown)
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest


class TestSmartCooldown:
    """Test dynamic cooldown based on trade profit/loss."""

    def _make_daemon_mock(self, active_positions=None, current_profit_pips=None):
        """Create a mock daemon with trade state."""
        daemon = MagicMock()
        daemon._tracked_positions = set(active_positions or [])
        daemon._last_execution_time = datetime.now() - timedelta(seconds=30)
        daemon._cooldown_seconds = 300
        daemon._last_close_time = None
        daemon._last_loss_time = None

        # Mock trade_state_engine
        trade_state = MagicMock()
        if current_profit_pips is not None:
            trade_state.current_profit_pips = current_profit_pips
        else:
            trade_state.current_profit_pips = 0.0

        reversal_model = MagicMock()
        reversal_model.trade_state_engine = MagicMock()
        reversal_model.trade_state_engine._state = trade_state

        daemon.reversal_model = reversal_model
        daemon.config = {"realtime_cooldown_seconds": 300}

        # Bind the method
        from axonai.realtime.daemon import AxonDaemon
        daemon._seconds_until_ready = AxonDaemon._seconds_until_ready.__get__(daemon)

        return daemon

    def test_no_active_position_fast_cooldown(self):
        """No active trade → 60 second fast recovery cooldown."""
        daemon = self._make_daemon_mock(active_positions=set())
        remaining = daemon._seconds_until_ready()

        # 30 seconds elapsed, 60 sec cooldown → ~30 sec remaining
        assert 25 < remaining < 35, f"Expected ~30s, got {remaining}s"

    def test_winning_trade_long_cooldown(self):
        """Winning trade (+3 pips) → 300 second protection cooldown."""
        daemon = self._make_daemon_mock(active_positions={123}, current_profit_pips=5.0)
        remaining = daemon._seconds_until_ready()

        # 30 seconds elapsed, 300 sec cooldown → ~270 sec remaining
        assert 265 < remaining < 275, f"Expected ~270s, got {remaining}s"

    def test_losing_trade_fast_cooldown(self):
        """Losing trade (-5 pips) → 60 second fast recovery cooldown."""
        daemon = self._make_daemon_mock(active_positions={123}, current_profit_pips=-5.0)
        remaining = daemon._seconds_until_ready()

        # 30 seconds elapsed, 60 sec cooldown → ~30 sec remaining
        assert 25 < remaining < 35, f"Expected ~30s, got {remaining}s"

    def test_breakeven_neutral_cooldown(self):
        """Breakeven/small loss → 120 second neutral cooldown."""
        daemon = self._make_daemon_mock(active_positions={123}, current_profit_pips=-1.0)
        remaining = daemon._seconds_until_ready()

        # 30 seconds elapsed, 120 sec cooldown → ~90 sec remaining
        assert 85 < remaining < 95, f"Expected ~90s, got {remaining}s"

    def test_small_profit_neutral_cooldown(self):
        """Small profit (+1 pip, <2) → 120 second neutral cooldown."""
        daemon = self._make_daemon_mock(active_positions={123}, current_profit_pips=1.0)
        remaining = daemon._seconds_until_ready()

        # 30 seconds elapsed, 120 sec cooldown → ~90 sec remaining
        assert 85 < remaining < 95, f"Expected ~90s, got {remaining}s"

    def test_threshold_exactly_winning(self):
        """Just above threshold: +2.1 pips → should trigger 300s cooldown."""
        daemon = self._make_daemon_mock(active_positions={123}, current_profit_pips=2.1)
        remaining = daemon._seconds_until_ready()

        # Should use 300 sec cooldown for winning (>2.0)
        assert 265 < remaining < 275, f"Expected ~270s for winning, got {remaining}s"

    def test_threshold_exactly_losing(self):
        """Just below threshold: -3.1 pips → should trigger 60s cooldown."""
        daemon = self._make_daemon_mock(active_positions={123}, current_profit_pips=-3.1)
        remaining = daemon._seconds_until_ready()

        # Should use 60 sec cooldown for losing (<-3.0)
        assert 25 < remaining < 35, f"Expected ~30s for losing, got {remaining}s"

    def test_cooldown_expires(self):
        """After cooldown expires, _seconds_until_ready returns 0."""
        daemon = self._make_daemon_mock(active_positions=set())
        # Set last execution 70 seconds ago (cooldown is 60 sec)
        daemon._last_execution_time = datetime.now() - timedelta(seconds=70)

        remaining = daemon._seconds_until_ready()
        assert remaining == 0.0, f"Expected 0s (expired), got {remaining}s"

    def test_multiple_active_positions_uses_first(self):
        """With multiple active positions, uses their collective state."""
        # Multiple positions but checking profit of the state object
        daemon = self._make_daemon_mock(active_positions={123, 124}, current_profit_pips=4.0)
        remaining = daemon._seconds_until_ready()

        # Should see 4 pips profit → 300 sec cooldown
        assert 265 < remaining < 275, f"Expected ~270s, got {remaining}s"

    def test_recovery_scenario_after_loss(self):
        """Scenario: Trade loses 5 pips, next signal within 60 sec should be allowed."""
        daemon = self._make_daemon_mock(active_positions={123}, current_profit_pips=-5.0)
        daemon._last_execution_time = datetime.now() - timedelta(seconds=61)

        remaining = daemon._seconds_until_ready()

        # 61 seconds elapsed, 60 sec cooldown → should be 0 (can execute)
        assert remaining <= 0.0, f"Trade loss should allow recovery after 61s, got {remaining}s"

    def test_protect_scenario_winning_trade(self):
        """Scenario: Trade wins +5 pips, cooldown should be long (300s)."""
        daemon = self._make_daemon_mock(active_positions={123}, current_profit_pips=5.0)
        daemon._last_execution_time = datetime.now() - timedelta(seconds=60)

        remaining = daemon._seconds_until_ready()

        # 60 seconds elapsed, 300 sec cooldown → ~240 sec remaining
        assert 235 < remaining < 245, f"Winning trade should protect 300s, got {remaining}s"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
