#!/usr/bin/env python3
"""
Live Reversal Calibrator (Deep Scan)

Connects to the MT5 Bridge stream and continuously ingests live tick data into
the advanced ReversalModel. Rather than trading, it tracks real-time market
movements and retrospectively tags "True Reversals". It also tracks "Fakeouts".

This Deep Scan version captures a comprehensive 20+ variable matrix of 
internal engine metrics (Velocity, Displacement, Regime, MTF, Liquidity, Location)
at the exact millisecond of the reversal event.

Generates both a Markdown summary and a Deep Scan CSV dataset (`reports/calibration_data.csv`).
"""

import sys
import os
import time
import logging
import csv
from collections import deque
from datetime import datetime

# Adjust path to find axonai modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from axonai.realtime.mt5_bridge_client import BridgeClient
from axonai.realtime.reversal_model import ReversalModel
from axonai.realtime.live_state import PriceLevel
from axonai.realtime.event_types import LiveCandle
from axonai.realtime.location_engine import LocationContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("DeepScanCalibrator")

class DummyDashboard:
    """Mock dashboard to force BridgeClient to broadcast candles/levels to us."""
    def __init__(self, parent):
        self.parent = parent
    def broadcast(self, data):
        self.parent.on_message(data)

class LiveReversalCalibrator:
    def __init__(self, symbol="EURUSD", bridge_host="127.0.0.1", bridge_port=8765, custom_url=None, continuous_log=False):
        self.symbol = symbol
        self.pip_mult = 0.01 if ("JPY" in symbol.upper() or "XAU" in symbol.upper()) else 0.0001
        self.bridge_host = bridge_host
        self.bridge_port = bridge_port
        self.custom_url = custom_url
        self.continuous_log = continuous_log
        
        self.model = ReversalModel(pip_mult=self.pip_mult, config={"symbol": self.symbol, "mt5_symbol": self.symbol})
        
        self.tick_buffer = deque(maxlen=3600)  # rough 1 hr
        self.true_reversals = []
        self.fakeouts = []
        
        self.local_high = (0.0, 0.0, None)  # (time_s, price, EngineSnapshot)
        self.local_low = (0.0, float('inf'), None)
        self.reversal_threshold_pips = 15.0
        
        self.last_trigger_time = 0.0
        self.trigger_cooldown_sec = 60.0
        
        self.active_triggers = []
        self.running = True
        
    def start(self):
        if self.continuous_log:
            os.makedirs("reports", exist_ok=True)
            self._continuous_file = open(f"reports/continuous_tick_data_{self.symbol}.csv", "a", newline="")
            self._continuous_writer = None  # Will be initialized on first tick when keys are known
            logger.info(f"Continuous logging enabled. Writing to reports/continuous_tick_data_{self.symbol}.csv")

        self.client = BridgeClient(
            host=self.bridge_host,
            port=self.bridge_port,
            dashboard_server=DummyDashboard(self),
            on_tick=self.on_tick,
            url=self.custom_url
        )
        url_str = self.custom_url if self.custom_url else f"ws://{self.bridge_host}:{self.bridge_port}"
        logger.info(f"Connecting Deep Scan Calibrator to MT5 stream at {url_str}...")
        self.client.start()
        
        try:
            while self.running:
                time.sleep(1)
                self.check_active_triggers()
        except KeyboardInterrupt:
            logger.info("Ctrl+C detected.")
        finally:
            self.running = False
            self.client._running = False
            self.generate_reports()
            if self.continuous_log and hasattr(self, '_continuous_file'):
                self._continuous_file.close()
            logger.info("Deep Scan Calibrator shutdown complete.")

    def on_message(self, data):
        msg_type = data.get("type")
        msg_sym = data.get("symbol")
        
        # Normalize and filter by symbol
        def clean_sym(s):
            return s.replace("m", "").replace("=X", "").upper() if s else ""
            
        if msg_sym and clean_sym(msg_sym) != clean_sym(self.symbol):
            return
            
        if msg_type == "candles":
            tf = data.get("timeframe", "H4")
            candles = data.get("candles", [])
            for c in candles:
                lc = LiveCandle(
                    timeframe=tf,
                    open_time=datetime.fromtimestamp(c["time"]),
                    open=c["open"], high=c["high"], low=c["low"], close=c["close"],
                    volume=1
                )
                self.model.on_candle_close(lc)
                
        elif msg_type == "levels":
            levels = data.get("levels", [])
            pls = []
            for lv in levels:
                pls.append(PriceLevel(
                    price=lv.get("price"),
                    level_type=lv.get("type", "ROUND"),
                    timeframe=lv.get("timeframe", "H4"),
                    touches=lv.get("touches", 0),
                    last_touch=datetime.utcnow(),
                    direction=lv.get("direction", "both"),
                    strength=lv.get("strength", 0.5),
                    is_active=True
                ))
            self.model.sync_levels(pls)
            
    def on_tick(self, data):
        t_sym = data.get("symbol")
        
        # Normalize and filter by symbol
        def clean_sym(s):
            return s.replace("m", "").replace("=X", "").upper() if s else ""
            
        if t_sym and clean_sym(t_sym) != clean_sym(self.symbol):
            return
            
        bid = data.get("bid")
        ask = data.get("ask")
        time_s = data.get("time")
        if not bid or not ask:
            return
            
        dt = datetime.fromtimestamp(time_s)
        
        # Fallback dummy location context
        loc = LocationContext(
            distance_to_liquidity=10.0,
            distance_to_sr=10.0,
            room_available=10.0,
            at_structure=False,
            nearest_level_type="NONE",
            nearest_level_price=0.0
        )
        
        snapshot = self.model.on_tick(
            price=bid,
            timestamp=dt,
            volume=1.0,
            location_context=loc,
            bid=bid,
            ask=ask
        )
        
        self.tick_buffer.append((time_s, bid, ask, snapshot))
        self.update_extrema(time_s, bid, snapshot)
        self.check_trigger(time_s, bid, snapshot)
        
        if self.continuous_log:
            row = self._extract_deep_scan("TICK", time_s, bid, snapshot)
            if self._continuous_writer is None:
                self._continuous_writer = csv.DictWriter(self._continuous_file, fieldnames=list(row.keys()))
                if self._continuous_file.tell() == 0:
                    self._continuous_writer.writeheader()
            self._continuous_writer.writerow(row)
            self._continuous_file.flush()
        
    def update_extrema(self, time_s, price, snapshot):
        if price > self.local_high[1]:
            self.local_high = (time_s, price, snapshot)
        if price < self.local_low[1]:
            self.local_low = (time_s, price, snapshot)
            
        if self.local_high[1] - price >= self.reversal_threshold_pips * self.pip_mult:
            logger.info(f"*** TRUE TOP DETECTED *** at {self.local_high[1]:.5f}")
            self.true_reversals.append({
                "type": "TOP",
                "timestamp": self.local_high[0],
                "price": self.local_high[1],
                "snapshot": self.local_high[2]
            })
            self.local_high = (time_s, price, snapshot)
            self.local_low = (time_s, price, snapshot)
            
        if price - self.local_low[1] >= self.reversal_threshold_pips * self.pip_mult:
            logger.info(f"*** TRUE BOTTOM DETECTED *** at {self.local_low[1]:.5f}")
            self.true_reversals.append({
                "type": "BOTTOM",
                "timestamp": self.local_low[0],
                "price": self.local_low[1],
                "snapshot": self.local_low[2]
            })
            self.local_high = (time_s, price, snapshot)
            self.local_low = (time_s, price, snapshot)
            
    def check_trigger(self, time_s, price, snapshot):
        if snapshot and snapshot.entry_decision:
            # Must be explicitly TRIGGERED, not just armed
            if snapshot.entry_decision.state == "TRIGGERED" and snapshot.entry_decision.direction in ("BUY", "SELL"):
                if time_s - self.last_trigger_time < self.trigger_cooldown_sec:
                    self.model.entry.reset()
                    return  # Prevent spam
                    
                self.last_trigger_time = time_s
                logger.warning(f"Engine Triggered {snapshot.entry_decision.direction} @ {price:.5f}")
                self.active_triggers.append({
                    "time": time_s,
                    "price": price,
                    "direction": snapshot.entry_decision.direction,
                    "snapshot": snapshot,
                    "max_favorable": 0.0,
                    "max_adverse": 0.0
                })
                self.model.entry.reset()
                
    def check_active_triggers(self):
        if not self.tick_buffer: return
        current_price = self.tick_buffer[-1][1]
        
        for trig in list(self.active_triggers):
            if trig["direction"] == "SELL":
                profit = (trig["price"] - current_price) / self.pip_mult
                adverse = (current_price - trig["price"]) / self.pip_mult
            else:
                profit = (current_price - trig["price"]) / self.pip_mult
                adverse = (trig["price"] - current_price) / self.pip_mult
                
            if profit > trig["max_favorable"]: trig["max_favorable"] = profit
            if adverse > trig["max_adverse"]: trig["max_adverse"] = adverse
            
            if trig["max_favorable"] >= self.reversal_threshold_pips:
                logger.info(f"Trigger SUCCESS: {trig['direction']} at {trig['price']:.5f}")
                # Optional: We could track successful triggers too!
                self.active_triggers.remove(trig)
            elif trig["max_adverse"] >= 10.0: 
                logger.info(f"Trigger FAKEOUT: {trig['direction']} at {trig['price']:.5f}")
                self.fakeouts.append(trig)
                self.active_triggers.remove(trig)

    def _extract_deep_scan(self, label, timestamp, price, snap):
        """Extracts a flat dictionary of 20+ engine metrics for CSV export."""
        row = {
            "Label": label,
            "Time": datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S'),
            "Price": price,
            
            # Velocity & Momentum
            "Vel_TickEfficiency": getattr(snap.velocity, "tick_efficiency", 0.0) if snap and snap.velocity else 0.0,
            "Vel_Acceleration": getattr(snap.velocity, "acceleration", 0.0) if snap and snap.velocity else 0.0,
            "Vel_DecayRatio": getattr(snap.velocity, "decay_ratio", 0.0) if snap and snap.velocity else 0.0,
            "Vel_ZScore": getattr(snap.velocity, "z_score", 0.0) if snap and snap.velocity else 0.0,
            "Vel_Rate10s": getattr(snap.velocity, "tick_rate_10s", 0.0) if snap and snap.velocity else 0.0,
            "Vel_Rate300s": getattr(snap.velocity, "tick_rate_300s", 0.0) if snap and snap.velocity else 0.0,
            
            # Displacement & Order Flow
            "Disp_Ratio": getattr(snap.displacement, "displacement_ratio", 0.0) if snap and snap.displacement else 0.0,
            "Disp_VolImbalance": getattr(snap.displacement, "volume_imbalance", 0.0) if snap and snap.displacement else 0.0,
            "Disp_Weighted": getattr(snap.displacement, "volume_weighted_displacement", 0.0) if snap and snap.displacement else 0.0,
            "Disp_Class": getattr(snap.displacement, "classification", "N/A") if snap and snap.displacement else "N/A",
            
            # Regime
            "Reg_Class": getattr(snap.regime, "regime", "N/A") if snap and snap.regime else "N/A",
            "Reg_TrendStrength": getattr(snap.regime, "trend_strength", 0.0) if snap and snap.regime else 0.0,
            "Reg_TransProb": getattr(snap.regime, "transition_probability", 0.0) if snap and snap.regime else 0.0,
            
            # MTF
            "MTF_H4Bias": getattr(snap.mtf, "h4_bias", 0.0) if snap and snap.mtf else 0.0,
            "MTF_H1Bias": getattr(snap.mtf, "h1_bias", 0.0) if snap and snap.mtf else 0.0,
            "MTF_AlignScore": getattr(snap.mtf, "alignment_score", 0.0) if snap and snap.mtf else 0.0,
            
            # Liquidity & Location
            "Liq_ActiveSweeps": len(getattr(snap.liquidity, "active_sweeps", [])) if snap and snap.liquidity else 0,
            "Liq_VoidActive": getattr(snap.liquidity, "liquidity_void_active", False) if snap and snap.liquidity else False,
            "Loc_AtStructure": getattr(snap.location_context, "at_structure", False) if snap and snap.location_context else False,
            "Loc_NearestLevel": getattr(snap.location_context, "nearest_level_type", "NONE") if snap and snap.location_context else "NONE",
            "Loc_RoomPips": getattr(snap.location_context, "room_available", 0.0) if snap and snap.location_context else 0.0,
        }
        return row

    def generate_reports(self):
        reports_dir = os.path.join(os.path.dirname(__file__), '../../reports')
        os.makedirs(reports_dir, exist_ok=True)
        
        md_path = os.path.join(reports_dir, f'calibration_report_{self.symbol}.md')
        csv_path = os.path.join(reports_dir, f'calibration_data_{self.symbol}.csv')
        
        # 1. Generate Markdown Summary
        with open(md_path, "w") as f:
            f.write("# Live Reversal Deep Scan Summary\n\n")
            f.write(f"**Generated at**: {datetime.now()}\n")
            f.write(f"**Target Threshold**: {self.reversal_threshold_pips} pips\n\n")
            f.write(f"- True Reversals Logged: {len(self.true_reversals)}\n")
            f.write(f"- Engine Fakeouts Logged: {len(self.fakeouts)}\n\n")
            f.write(f"Full metrics matrix exported to: `{csv_path}`\n")
            
        # 2. Generate CSV Deep Scan Matrix
        all_rows = []
        for rev in self.true_reversals:
            all_rows.append(self._extract_deep_scan(f"TRUE_{rev['type']}", rev['timestamp'], rev['price'], rev['snapshot']))
            
        for fo in self.fakeouts:
            all_rows.append(self._extract_deep_scan(f"FAKEOUT_{fo['direction']}", fo['time'], fo['price'], fo['snapshot']))
            
        if all_rows:
            keys = all_rows[0].keys()
            with open(csv_path, "w", newline='') as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(all_rows)
                
        logger.info(f"Deep Scan reports saved to {reports_dir}")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dashboard", action="store_true", help="Connect to local dashboard websocket (ws://127.0.0.1:8000/ws) instead of bridge")
    parser.add_argument("--url", type=str, default=None, help="Custom WebSocket URL")
    parser.add_argument("--continuous", action="store_true", help="Continuously write every tick and its deep scan metrics to a CSV")
    parser.add_argument("--symbol", type=str, default="EURUSD", help="The target symbol to run calibration on")
    args = parser.parse_args()
    
    bridge_host = os.environ.get("BRIDGE_HOST")
    if not bridge_host:
        if sys.platform == "win32":
            bridge_host = "127.0.0.1"
        else:
            import subprocess
            try:
                res = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=2)
                bridge_host = res.stdout.strip().split()[2]
            except:
                bridge_host = "127.0.0.1"
                
    custom_url = args.url
    if args.dashboard and not custom_url:
        custom_url = "ws://127.0.0.1:8000/ws"
                
    calib = LiveReversalCalibrator(symbol=args.symbol, bridge_host=bridge_host, custom_url=custom_url, continuous_log=args.continuous)
    calib.start()
