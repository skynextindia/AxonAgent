import logging
import requests
import os
import json
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
from bs4 import BeautifulSoup
import pytz

logger = logging.getLogger(__name__)

class CalendarGuard:
    """Monitors upcoming economic calendar events from ForexFactory.

    Blocks new trade entries and closes active positions before significant events,
    and measures historical outcomes against market reaction.
    """

    def __init__(self, symbol: str, config: dict):
        self.symbol = symbol
        self.config = config
        self.enabled = config.get("realtime_calendar_enabled", True)
        
        impacts = config.get("realtime_calendar_impacts", ["High", "Medium", "Low"])
        self.impacts = [imp.lower() for imp in impacts]
        self.currencies = self._get_symbol_currencies(symbol)
        self.url_thisweek = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        self.url_nextweek = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"
        
        # Dynamic buffers based on volatility (Low, Medium, High)
        # Default block_after to 30 to align with requirement "trading will be enabled after 30 Minutes of Event"
        self.impact_configs = {
            "high": {
                "block_before": config.get("realtime_calendar_high_block_before", 15),
                "block_after": config.get("realtime_calendar_high_block_after", 30),
                "cut_before": config.get("realtime_calendar_high_cut_before", 15)
            },
            "medium": {
                "block_before": config.get("realtime_calendar_medium_block_before", 10),
                "block_after": config.get("realtime_calendar_medium_block_after", 30),
                "cut_before": config.get("realtime_calendar_medium_cut_before", 10)
            },
            "low": {
                "block_before": config.get("realtime_calendar_low_block_before", 5),
                "block_after": config.get("realtime_calendar_low_block_after", 30),
                "cut_before": config.get("realtime_calendar_low_cut_before", 5)
            }
        }
        
        self.events: List[Dict[str, Any]] = []
        self.last_fetch_time: Optional[datetime] = None
        self.fetch_interval_seconds = 3600  # Fetch once every hour
        
        # Keep track of analyzed events to prevent duplicates
        self.analyzed_event_ids = set()
        self.load_analyzed_outcomes()

        logger.info(
            "CalendarGuard initialized for %s. Currencies: %s. Impacts to block: %s. "
            "Buffers (Before/After/Cut): High: %d/%d/%d min, Medium: %d/%d/%d min, Low: %d/%d/%d min",
            symbol, self.currencies, self.impacts,
            self.impact_configs["high"]["block_before"], self.impact_configs["high"]["block_after"], self.impact_configs["high"]["cut_before"],
            self.impact_configs["medium"]["block_before"], self.impact_configs["medium"]["block_after"], self.impact_configs["medium"]["cut_before"],
            self.impact_configs["low"]["block_before"], self.impact_configs["low"]["block_after"], self.impact_configs["low"]["cut_before"]
        )

    def _get_symbol_currencies(self, symbol: str) -> List[str]:
        """Extract individual currency codes from symbol."""
        clean = symbol.replace("=X", "").replace("=x", "").strip()
        if len(clean) >= 6:
            return [clean[:3].upper(), clean[3:6].upper()]
        return [clean.upper()]

    def load_analyzed_outcomes(self):
        """Load already analyzed events from outcomes file to avoid duplicate logs."""
        outcomes_path = os.path.join("reports", "calendar_outcomes.jsonl")
        if not os.path.exists(outcomes_path):
            return
        try:
            with open(outcomes_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            data = json.loads(line)
                            key = f"{data.get('event_title')}_{data.get('event_country')}_{data.get('event_time')}"
                            self.analyzed_event_ids.add(key)
                        except Exception:
                            continue
            logger.info("CalendarGuard: Loaded %d already analyzed events.", len(self.analyzed_event_ids))
        except Exception as e:
            logger.error("CalendarGuard: Error loading analyzed outcomes: %s", e)

    def update(self) -> bool:
        """Check if economic calendar needs to be fetched and update it.

        Returns:
            True if a new fetch was performed, False otherwise.
        """
        if not self.enabled:
            return False
            
        now = datetime.now(timezone.utc)
        if self.last_fetch_time is None or (now - self.last_fetch_time).total_seconds() >= self.fetch_interval_seconds:
            self.fetch_calendar()
            return True
        return False

    def fetch_calendar(self):
        """Fetch economic calendar events by scraping ForexFactory for the simulated month."""
        sim_now = datetime.now(timezone.utc)
        month_str = sim_now.strftime("%b.%Y").lower() # e.g. 'jun.2026'
        url = f"https://www.forexfactory.com/calendar?month={month_str}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5"
        }
        
        try:
            logger.info("CalendarGuard: Fetching news events from %s", url)
            res = requests.get(url, headers=headers, timeout=15)
            res.raise_for_status()
        except Exception as e:
            logger.error("CalendarGuard: Failed to fetch calendar HTML: %s", e)
            return

        soup = BeautifulSoup(res.text, "html.parser")
        rows = soup.select("tr.calendar__row")
        
        parsed_events = []
        current_date_str = ""
        current_time_text = ""
        year = sim_now.year
        est = pytz.timezone("US/Eastern")
        
        major_currencies = {"USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "CNY"}
        
        for row in rows:
            date_el = row.select_one(".calendar__date")
            if date_el and date_el.text.strip():
                parts = date_el.text.strip().split(" ")
                if len(parts) >= 3:
                    current_date_str = f"{parts[1]} {parts[2]}"
                    
            if not current_date_str:
                continue
                
            time_el = row.select_one(".calendar__time")
            time_text = time_el.text.strip() if time_el else ""
            
            if time_text:
                current_time_text = time_text
            else:
                time_text = current_time_text
                
            if not time_text or "All Day" in time_text or "Tentative" in time_text or "Day" in time_text:
                continue
                
            currency_el = row.select_one(".calendar__currency")
            ev_currency = currency_el.text.strip().upper() if currency_el else ""
            
            if ev_currency not in major_currencies:
                continue
                
            impact_el = row.select_one(".calendar__impact span")
            impact_class = " ".join(impact_el["class"]) if impact_el and impact_el.has_attr("class") else ""
            ev_impact = "low"
            if "red" in impact_class: ev_impact = "high"
            elif "ora" in impact_class: ev_impact = "medium"
            elif "gra" in impact_class: ev_impact = "non-economic"
            
            if ev_impact not in self.impacts:
                continue
                
            event_el = row.select_one(".calendar__event")
            if not event_el or not event_el.text.strip():
                continue
            event_title = event_el.text.strip()
            
            forecast_el = row.select_one(".calendar__forecast")
            forecast = forecast_el.text.strip() if forecast_el else ""
            previous_el = row.select_one(".calendar__previous")
            previous = previous_el.text.strip() if previous_el else ""
            actual_el = row.select_one(".calendar__actual")
            actual = actual_el.text.strip() if actual_el else ""
            
            dt_str = ""
            try:
                dt_str = f"{current_date_str} {year} {time_text}"
                local_dt = datetime.strptime(dt_str, "%b %d %Y %I:%M%p")
                local_dt = est.localize(local_dt)
                event_time = local_dt.astimezone(timezone.utc)
            except Exception as parse_err:
                logger.debug("CalendarGuard: Failed to parse event date '%s': %s", dt_str, parse_err)
                continue
                
            parsed_events.append({
                "title": event_title,
                "country": ev_currency,
                "impact": ev_impact.capitalize(),
                "time": event_time,
                "forecast": forecast,
                "previous": previous,
                "actual": actual
            })
            
        parsed_events.sort(key=lambda x: x["time"])
        self.events = parsed_events
        self.last_fetch_time = datetime.now(timezone.utc)
        logger.info("CalendarGuard: Successfully scraped and parsed %d matching events.", len(self.events))

    def check_block_status(self) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Check if trading is currently blocked due to an upcoming or active news event.

        Trading remains blocked until exactly 30 minutes after the event passes.

        Returns:
            (is_blocked, active_event, reason)
        """
        if not self.enabled:
            return False, None, ""
            
        self.update()
        now = datetime.now(timezone.utc)
        
        for ev in self.events:
            # Filter by currencies of the active traded pair for security/blocking
            if ev["country"].upper() not in self.currencies:
                continue
            ev_time = ev["time"]
            impact = ev["impact"].lower()
            cfg = self.impact_configs.get(impact, self.impact_configs["medium"])
            
            start_block = ev_time - timedelta(minutes=cfg["block_before"])
            # Enforce strict 30-minute block window after event as requested
            block_after_mins = max(30, cfg.get("block_after", 30))
            end_block = ev_time + timedelta(minutes=block_after_mins)
            
            if start_block <= now <= end_block:
                time_diff_mins = (ev_time - now).total_seconds() / 60.0
                if time_diff_mins > 0:
                    reason = f"Blocked: {ev['impact']} event '{ev['title']}' ({ev['country']}) in {time_diff_mins:.1f} minutes (Buffer: {cfg['block_before']}m)"
                else:
                    reason = f"Blocked: {ev['impact']} event '{ev['title']}' ({ev['country']}) occurred {-time_diff_mins:.1f} minutes ago (Buffer: {block_after_mins}m)"
                return True, ev, reason
                
        return False, None, ""

    def check_cut_status(self, is_in_profit: bool = False, is_near_level: bool = False) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Check if active positions should be cut/closed before an upcoming event.

        Returns:
            (should_cut, active_event, reason)
        """
        if not self.enabled:
            return False, None, ""
            
        self.update()
        now = datetime.now(timezone.utc)
        
        for ev in self.events:
            # Filter by currencies of the active traded pair for security/position cutting
            if ev["country"].upper() not in self.currencies:
                continue
            ev_time = ev["time"]
            impact = ev["impact"].lower()
            cfg = self.impact_configs.get(impact, self.impact_configs["medium"])
            
            cut_before_mins = cfg["cut_before"]
            if is_in_profit or is_near_level:
                extra_buffer = self.config.get("realtime_calendar_profit_level_buffer", 5)
                cut_before_mins += extra_buffer
                
            start_cut = ev_time - timedelta(minutes=cut_before_mins)
            
            if start_cut <= now < ev_time:
                time_diff_mins = (ev_time - now).total_seconds() / 60.0
                reason = f"Cut position: {ev['impact']} event '{ev['title']}' ({ev['country']}) in {time_diff_mins:.1f} minutes (Buffer: {cut_before_mins}m, Profit: {is_in_profit}, Level: {is_near_level})"
                return True, ev, reason
                
        return False, None, ""

    def _get_expected_news_direction(self, event: dict) -> str:
        """Determines expected news direction (BUY/SELL/NEUTRAL/UNKNOWN) based on actual vs forecast."""
        actual = event.get("actual")
        forecast = event.get("forecast")
        country = event.get("country", "").upper()
        
        if not actual or not forecast:
            return "UNKNOWN"
            
        def parse_val(v):
            try:
                clean = "".join(c for c in str(v) if c.isdigit() or c in (".", "-"))
                return float(clean)
            except Exception:
                return None
                
        act_num = parse_val(actual)
        fc_num = parse_val(forecast)
        if act_num is None or fc_num is None:
            return "UNKNOWN"
            
        diff = act_num - fc_num
        if abs(diff) < 1e-6:
            return "NEUTRAL"
            
        title = event.get("title", "").lower()
        higher_is_bad = any(x in title for x in ["unemployment", "jobless", "claims", "deficit"])
        is_positive = diff > 0 if not higher_is_bad else diff < 0
        
        sym = self.symbol.upper()
        base = sym[:3]
        quote = sym[3:6] if len(sym) >= 6 else ""
        
        if country == base:
            return "BUY" if is_positive else "SELL"
        elif country == quote:
            return "SELL" if is_positive else "BUY"
            
        return "UNKNOWN"

    def analyze_completed_events(self, mt5_symbol: str, offset_hours: float):
        """Checks for events that completed at least 30 minutes ago, measures market outcome,

        and saves reports to reports/calendar_outcomes.jsonl.
        """
        import MetaTrader5 as mt5
        if not mt5 or not mt5.terminal_info():
            return
            
        now = datetime.now(timezone.utc)
        outcomes_path = os.path.join("reports", "calendar_outcomes.jsonl")
        os.makedirs("reports", exist_ok=True)
        
        for ev in self.events:
            ev_time = ev["time"]
            key = f"{ev['title']}_{ev['country']}_{ev_time.strftime('%Y-%m-%d %H:%M:%S')}"
            
            # Skip if already analyzed, no actual value yet, or not yet 30 minutes after event
            if key in self.analyzed_event_ids:
                continue
            if not ev.get("actual"):
                continue
            if now < ev_time + timedelta(minutes=30):
                continue
                
            expected_dir = self._get_expected_news_direction(ev)
            
            # Convert UTC event time to broker timezone for MT5 rates fetching
            broker_ev_time = ev_time + timedelta(hours=offset_hours)
            broker_ev_time_naive = broker_ev_time.replace(tzinfo=None)
            
            # Fetch M1 rates from event time to +30 minutes (request 45 bars to be safe)
            rates = mt5.copy_rates_from(mt5_symbol, mt5.TIMEFRAME_M1, broker_ev_time_naive + timedelta(minutes=32), 45)
            if rates is None or len(rates) < 30:
                logger.warning("CalendarGuard: Failed to fetch M1 rates for event '%s' analysis.", ev["title"])
                continue
                
            # Convert target times to broker epoch timestamps for matching
            t_event = int(broker_ev_time_naive.replace(tzinfo=timezone.utc).timestamp())
            t_5m = t_event + 5 * 60
            t_15m = t_event + 15 * 60
            t_30m = t_event + 30 * 60
            
            def find_closest_price(rates_list, target_epoch):
                best_price = None
                best_diff = float("inf")
                for bar in rates_list:
                    bar_time = int(bar["time"])
                    diff = abs(bar_time - target_epoch)
                    if diff < best_diff and diff <= 120:  # within 2 mins
                        best_diff = diff
                        best_price = float(bar["open"])
                return best_price
                
            price_baseline = find_closest_price(rates, t_event)
            price_5m = find_closest_price(rates, t_5m)
            price_15m = find_closest_price(rates, t_15m)
            price_30m = find_closest_price(rates, t_30m)
            
            if price_baseline is None or price_5m is None or price_15m is None or price_30m is None:
                logger.warning("CalendarGuard: Missing M1 bars for some outcome intervals of event '%s'.", ev["title"])
                continue
                
            # Determine pip size
            sym = mt5_symbol.upper()
            if "XAU" in sym or "GOLD" in sym:
                pip_size = 0.1
            elif "JPY" in sym:
                pip_size = 0.01
            else:
                pip_size = 0.0001
                
            pips_5m = (price_5m - price_baseline) / pip_size
            pips_15m = (price_15m - price_baseline) / pip_size
            pips_30m = (price_30m - price_baseline) / pip_size
            
            def get_alignment(act_pips, exp_dir):
                if exp_dir == "UNKNOWN" or exp_dir == "NEUTRAL":
                    return "NEUTRAL"
                if abs(act_pips) < 1.0:
                    return "NEUTRAL"
                act_dir = "BUY" if act_pips > 0 else "SELL"
                return "ALIGNED" if act_dir == exp_dir else "CONTRA"
                
            align_5m = get_alignment(pips_5m, expected_dir)
            align_15m = get_alignment(pips_15m, expected_dir)
            align_30m = get_alignment(pips_30m, expected_dir)
            
            log_payload = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "event_title": ev["title"],
                "event_country": ev["country"],
                "event_time": ev_time.strftime("%Y-%m-%d %H:%M:%S"),
                "actual": ev["actual"],
                "forecast": ev["forecast"],
                "previous": ev["previous"],
                "expected_direction": expected_dir,
                "prices": {
                    "baseline": round(price_baseline, 5),
                    "at_5m": round(price_5m, 5),
                    "at_15m": round(price_15m, 5),
                    "at_30m": round(price_30m, 5)
                },
                "pips_moved": {
                    "at_5m": round(pips_5m, 1),
                    "at_15m": round(pips_15m, 1),
                    "at_30m": round(pips_30m, 1)
                },
                "alignments": {
                    "at_5m": align_5m,
                    "at_15m": align_15m,
                    "at_30m": align_30m
                }
            }
            
            try:
                with open(outcomes_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(log_payload) + "\n")
                self.analyzed_event_ids.add(key)
                
                # Log summary message
                msg = (f"NEWS OUTCOME: '{log_payload['event_title']}' ({log_payload['event_country']}) "
                       f"| Expected: {log_payload['expected_direction']} "
                       f"| Move 5m: {log_payload['pips_moved']['at_5m']:+.1f} ({log_payload['alignments']['at_5m']}) "
                       f"| Move 15m: {log_payload['pips_moved']['at_15m']:+.1f} ({log_payload['alignments']['at_15m']}) "
                       f"| Move 30m: {log_payload['pips_moved']['at_30m']:+.1f} ({log_payload['alignments']['at_30m']})")
                logger.info("=" * 60)
                logger.info(msg)
                logger.info("=" * 60)
                
                with open(os.path.join("reports", "signals.log"), "a", encoding="utf-8") as f:
                    f.write(f"[{log_payload['timestamp']}] {msg}\n")
            except Exception as le:
                logger.error("CalendarGuard: Failed to write outcome: %s", le)
