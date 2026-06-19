import pytest
import os
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from axonai.realtime.calendar_guard import CalendarGuard

@pytest.fixture
def mock_config():
    return {
        "realtime_calendar_enabled": True,
        "realtime_calendar_impacts": ["High", "Medium"],
        "realtime_calendar_high_block_before": 15,
        "realtime_calendar_high_block_after": 30,
        "realtime_calendar_high_cut_before": 15,
        "realtime_calendar_medium_block_before": 10,
        "realtime_calendar_medium_block_after": 30,
        "realtime_calendar_medium_cut_before": 10,
        "realtime_calendar_low_block_before": 5,
        "realtime_calendar_low_block_after": 30,
        "realtime_calendar_low_cut_before": 5,
        "realtime_calendar_profit_level_buffer": 5,
    }

def test_currencies_extraction(mock_config):
    guard = CalendarGuard("EURUSD", mock_config)
    assert guard.currencies == ["EUR", "USD"]
    
    guard_xau = CalendarGuard("XAUUSD", mock_config)
    assert guard_xau.currencies == ["XAU", "USD"]

def test_news_direction_classification(mock_config):
    guard = CalendarGuard("EURUSD", mock_config)
    
    # 1. EUR event - Higher is good (e.g. GDP)
    ev_gdp_pos = {
        "title": "GDP q/q",
        "country": "EUR",
        "actual": "0.5%",
        "forecast": "0.2%"
    }
    assert guard._get_expected_news_direction(ev_gdp_pos) == "BUY"
    
    ev_gdp_neg = {
        "title": "GDP q/q",
        "country": "EUR",
        "actual": "0.1%",
        "forecast": "0.2%"
    }
    assert guard._get_expected_news_direction(ev_gdp_neg) == "SELL"

    # 2. USD event - Higher is good (e.g. Retail Sales) -> Drives EURUSD down
    ev_sales_pos = {
        "title": "Retail Sales m/m",
        "country": "USD",
        "actual": "0.8%",
        "forecast": "0.4%"
    }
    assert guard._get_expected_news_direction(ev_sales_pos) == "SELL"
    
    # 3. USD event - Higher is bad (e.g. Unemployment Rate) -> Positive surprise in unemployment = bad surprise -> USD weak -> EURUSD up
    ev_unemp_pos = {
        "title": "Unemployment Rate",
        "country": "USD",
        "actual": "4.2%",  # higher than forecast = worse surprise
        "forecast": "4.0%"
    }
    assert guard._get_expected_news_direction(ev_unemp_pos) == "BUY"  # since higher is bad, diff is positive (bad), so is_positive is False -> USD weak -> EURUSD BUY

def test_block_status(mock_config):
    guard = CalendarGuard("EURUSD", mock_config)
    # Prevent real network fetch
    guard.last_fetch_time = datetime.now(timezone.utc)
    
    # Setup mock event 10 minutes in the future
    now = datetime.now(timezone.utc)
    ev_time = now + timedelta(minutes=10)
    
    guard.events = [{
        "title": "Interest Rate Decision",
        "country": "USD",
        "impact": "High",
        "time": ev_time,
        "forecast": "5.25%",
        "previous": "5.25%",
        "actual": None
    }]
    
    is_blocked, ev, reason = guard.check_block_status()
    assert is_blocked is True
    assert ev["title"] == "Interest Rate Decision"
    assert "Blocked" in reason

    # Past event: 25 minutes ago (still blocked because after buffer is 30 mins)
    ev_time_past = now - timedelta(minutes=25)
    guard.events[0]["time"] = ev_time_past
    is_blocked, ev, reason = guard.check_block_status()
    assert is_blocked is True
    
    # Past event: 35 minutes ago (should NOT be blocked)
    ev_time_passed = now - timedelta(minutes=35)
    guard.events[0]["time"] = ev_time_passed
    is_blocked, ev, reason = guard.check_block_status()
    assert is_blocked is False

def test_cut_status(mock_config):
    guard = CalendarGuard("EURUSD", mock_config)
    # Prevent real network fetch
    guard.last_fetch_time = datetime.now(timezone.utc)
    
    now = datetime.now(timezone.utc)
    
    # Event 12 minutes away. Medium cut window is 10 min. (Should not cut normally)
    ev_time = now + timedelta(minutes=12)
    guard.events = [{
        "title": "CPI",
        "country": "USD",
        "impact": "Medium",
        "time": ev_time,
    }]
    should_cut, ev, reason = guard.check_cut_status()
    assert should_cut is False
    
    # But if in profit, extra buffer (+5m) applies -> cut window becomes 15m -> should cut!
    should_cut_profit, ev, reason = guard.check_cut_status(is_in_profit=True)
    assert should_cut_profit is True
    assert "Cut position" in reason

def test_outcome_tracking(mock_config, tmp_path):
    guard = CalendarGuard("EURUSD", mock_config)
    # Prevent real network fetch
    guard.last_fetch_time = datetime.now(timezone.utc)
    
    now = datetime.now(timezone.utc)
    ev_time = now - timedelta(minutes=35)  # happened 35 minutes ago
    
    guard.events = [{
        "title": "CPI m/m",
        "country": "USD",
        "impact": "High",
        "time": ev_time,
        "forecast": "0.2%",
        "previous": "0.1%",
        "actual": "0.4%" # actual > forecast -> USD strong -> EURUSD goes down (SELL)
    }]
    
    # Custom join function to redirect outputs correctly without recursion
    original_join = os.path.join
    tmp_path_str = str(tmp_path)
    def mock_join(a, b, *args):
        if "calendar_outcomes.jsonl" in b:
            return original_join(tmp_path_str, "calendar_outcomes.jsonl")
        return original_join(a, b, *args)

    # Mock MT5
    with patch("MetaTrader5.terminal_info", return_value=True), \
         patch("MetaTrader5.copy_rates_from") as mock_copy, \
         patch("axonai.realtime.calendar_guard.os.path.join", side_effect=mock_join):
        
        # M1 rates mock. Bar times represent broker time.
        # Suppose broker offset is 0.
        epoch_event = int(ev_time.replace(tzinfo=None).replace(tzinfo=timezone.utc).timestamp())
        
        # We simulate price going down (as USD surprise is positive, base EURUSD should fall)
        mock_rates = []
        for i in range(45):
            bar_time = epoch_event - 2 * 60 + i * 60  # start 2 mins before
            # base price is 1.1000
            price = 1.1000
            if bar_time >= epoch_event + 30 * 60:
                price = 1.0970  # -30 pips at 30m
            elif bar_time >= epoch_event + 15 * 60:
                price = 1.0980  # -20 pips at 15m
            elif bar_time >= epoch_event + 5 * 60:
                price = 1.0990  # -10 pips at 5m
            
            mock_rates.append({
                "time": bar_time,
                "open": price,
                "high": price + 0.0001,
                "low": price - 0.0001,
                "close": price
            })
            
        mock_copy.return_value = mock_rates
        
        guard.analyze_completed_events("EURUSD", 0)
        
        # Verify result was written to outcomes JSONL file
        outcomes_file = os.path.join(tmp_path, "calendar_outcomes.jsonl")
        assert os.path.exists(outcomes_file)
        
        with open(outcomes_file, "r") as f:
            lines = f.readlines()
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["event_title"] == "CPI m/m"
            assert data["expected_direction"] == "SELL"
            assert data["pips_moved"]["at_5m"] == -10.0
            assert data["pips_moved"]["at_15m"] == -20.0
            assert data["pips_moved"]["at_30m"] == -30.0
            assert data["alignments"]["at_5m"] == "ALIGNED"
            assert data["alignments"]["at_15m"] == "ALIGNED"
            assert data["alignments"]["at_30m"] == "ALIGNED"

def test_calendar_rollover(mock_config):
    import pytz
    guard = CalendarGuard("EURUSD", mock_config)
    
    now = datetime.now(timezone.utc)
    ev_time = now - timedelta(hours=1)
    
    # Format ev_time in US/Eastern timezone to match scraper parsing expectation
    est = pytz.timezone("US/Eastern")
    ev_time_est = ev_time.astimezone(est)
    date_str = ev_time_est.strftime("%a %b %d") # e.g. "Thu May 07"
    time_str = ev_time_est.strftime("%I:%M%p").lower() # e.g. "10:01am"
    
    html_content = f"""
    <table>
      <tr class="calendar__row">
        <td class="calendar__date"> {date_str} </td>
        <td class="calendar__time"> {time_str} </td>
        <td class="calendar__currency"> USD </td>
        <td class="calendar__impact"> <span class="red"></span> </td>
        <td class="calendar__event"> Expired Test Event </td>
        <td class="calendar__forecast"> 1.0% </td>
        <td class="calendar__previous"> 1.0% </td>
        <td class="calendar__actual"> 1.2% </td>
      </tr>
    </table>
    """
    
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.text = html_content
        mock_get.return_value = mock_response
        
        guard.fetch_calendar()
        
        assert len(guard.events) == 2
        original = guard.events[0]
        cloned = guard.events[1]
        
        assert original["title"] == "Expired Test Event"
        assert cloned["title"] == "Expired Test Event"
        assert cloned["actual"] is None
        # Assert cloned time is original time + 7 days
        # Since original and cloned times are localized to UTC from US/Eastern times,
        # let's assert their difference is exactly 7 days.
        assert cloned["time"] == original["time"] + timedelta(days=7)
        assert cloned["time"] > now
