"""Test smart dynamic cooldown logic.

Smart cooldown adjusts based on trade outcome (aggressive settings):
- No active trade: 30 sec (fast recovery)
- Winning trade (+2+ pips): 120 sec (protect profit)
- Losing trade (-3+ pips): 20 sec (quick recovery)
- Breakeven/small loss: 45 sec (neutral cooldown)
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
        daemon._last_execution_time = datetime.now() - timedelta(seconds=10)
        daemon._cooldown_seconds = 120
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
        daemon.config = {"realtime_cooldown_seconds": 120}

        # Bind the method
        from axonai.realtime.daemon import AxonDaemon
        daemon._seconds_until_ready = AxonDaemon._seconds_until_ready.__get__(daemon)

        return daemon

    def test_no_active_position_fast_cooldown(self):
        """No active trade → 30 second fast recovery cooldown."""
        daemon = self._make_daemon_mock(active_positions=set())
        remaining = daemon._seconds_until_ready()

        # 10 seconds elapsed, 30 sec cooldown → ~20 sec remaining
        assert 15 < remaining < 25, f"Expected ~20s, got {remaining}s"

    def test_winning_trade_long_cooldown(self):
        """Winning trade (+3 pips) → 120 second protection cooldown."""
        daemon = self._make_daemon_mock(active_positions={123}, current_profit_pips=5.0)
        remaining = daemon._seconds_until_ready()

        # 10 seconds elapsed, 120 sec cooldown → ~110 sec remaining
        assert 105 < remaining < 115, f"Expected ~110s, got {remaining}s"

    def test_losing_trade_fast_cooldown(self):
        """Losing trade (-5 pips) → 20 second fast recovery cooldown."""
        daemon = self._make_daemon_mock(active_positions={123}, current_profit_pips=-5.0)
        remaining = daemon._seconds_until_ready()

        # 10 seconds elapsed, 20 sec cooldown → ~10 sec remaining
        assert 5 < remaining < 15, f"Expected ~10s, got {remaining}s"

    def test_breakeven_neutral_cooldown(self):
        """Breakeven/small loss → 45 second neutral cooldown."""
        daemon = self._make_daemon_mock(active_positions={123}, current_profit_pips=-1.0)
        remaining = daemon._seconds_until_ready()

        # 10 seconds elapsed, 45 sec cooldown → ~35 sec remaining
        assert 30 < remaining < 40, f"Expected ~35s, got {remaining}s"

    def test_small_profit_neutral_cooldown(self):
        """Small profit (+1 pip, <2) → 45 second neutral cooldown."""
        daemon = self._make_daemon_mock(active_positions={123}, current_profit_pips=1.0)
        remaining = daemon._seconds_until_ready()

        # 10 seconds elapsed, 45 sec cooldown → ~35 sec remaining
        assert 30 < remaining < 40, f"Expected ~35s, got {remaining}s"

    def test_threshold_exactly_winning(self):
        """Just above threshold: +2.1 pips → should trigger 120s cooldown."""
        daemon = self._make_daemon_mock(active_positions={123}, current_profit_pips=2.1)
        remaining = daemon._seconds_until_ready()

        # Should use 120 sec cooldown for winning (>2.0)
        assert 105 < remaining < 115, f"Expected ~110s for winning, got {remaining}s"

    def test_threshold_exactly_losing(self):
        """Just below threshold: -3.1 pips → should trigger 20s cooldown."""
        daemon = self._make_daemon_mock(active_positions={123}, current_profit_pips=-3.1)
        remaining = daemon._seconds_until_ready()

        # Should use 20 sec cooldown for losing (<-3.0)
        assert 5 < remaining < 15, f"Expected ~10s for losing, got {remaining}s"

    def test_cooldown_expires(self):
        """After cooldown expires, _seconds_until_ready returns 0."""
        daemon = self._make_daemon_mock(active_positions=set())
        # Set last execution 40 seconds ago (cooldown is 30 sec)
        daemon._last_execution_time = datetime.now() - timedelta(seconds=40)

        remaining = daemon._seconds_until_ready()
        assert remaining == 0.0, f"Expected 0s (expired), got {remaining}s"

    def test_multiple_active_positions_uses_first(self):
        """With multiple active positions, uses their collective state."""
        # Multiple positions but checking profit of the state object
        daemon = self._make_daemon_mock(active_positions={123, 124}, current_profit_pips=4.0)
        remaining = daemon._seconds_until_ready()

        # Should see 4 pips profit → 120 sec cooldown
        assert 105 < remaining < 115, f"Expected ~110s, got {remaining}s"

    def test_recovery_scenario_after_loss(self):
        """Scenario: Trade loses 5 pips, next signal within 20 sec should be allowed."""
        daemon = self._make_daemon_mock(active_positions={123}, current_profit_pips=-5.0)
        daemon._last_execution_time = datetime.now() - timedelta(seconds=21)

        remaining = daemon._seconds_until_ready()

        # 21 seconds elapsed, 20 sec cooldown → should be 0 (can execute)
        assert remaining <= 0.0, f"Trade loss should allow recovery after 21s, got {remaining}s"

    def test_protect_scenario_winning_trade(self):
        """Scenario: Trade wins +5 pips, cooldown should be long (120s)."""
        daemon = self._make_daemon_mock(active_positions={123}, current_profit_pips=5.0)
        daemon._last_execution_time = datetime.now() - timedelta(seconds=10)

        remaining = daemon._seconds_until_ready()

        # 10 seconds elapsed, 120 sec cooldown → ~110 sec remaining
        assert 105 < remaining < 115, f"Winning trade should protect 120s, got {remaining}s"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
