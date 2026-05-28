"""The main orchestrator for the algorithmic trading system with WebUI integration."""

import os
import sys
import time
import logging
from typing import List, Dict, Optional
import MetaTrader5 as mt5

# Ensure local path takes priority for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mt5_receiver import TickReceiver
from tick_engine import TickBuffer, TickProcessor, SignalState
from market_state import MarketStateMachine, StateTransition
from market_context import MarketContextBuilder
from llm_bridge import LLMBridge
from execution import ExecutionEngine

logger = logging.getLogger(__name__)

class TradingOrchestrator:
    """The central trading engine orchestrating all real-time data flow and operations."""
    def __init__(self, api_key: str, symbols: List[str] = ["EURUSD", "GBPUSD"], dry_run: bool = True, debug: bool = False):
        self.symbols = symbols
        self.debug = debug
        self.config = {
            "tick_poll_interval_ms": 100,
            "realtime_cooldown_seconds": 60,
            "realtime_min_event_priority": "MEDIUM"
        }
        
        # Capture startup time
        self._start_time = time.time()
        
        # Instantiate subcomponents
        self.buffers = {s: TickBuffer() for s in symbols}
        self.processors = {s: TickProcessor() for s in symbols}
        self.state_machines = {s: MarketStateMachine(debug=debug) for s in symbols}
        self.context_builder = MarketContextBuilder()
        self.llm = LLMBridge(api_key=api_key, debug=debug)
        self.execution = ExecutionEngine(dry_run=dry_run, debug=debug)
        
        # Receiver needs to be initialized with original symbol list
        self.receiver = TickReceiver(symbols, debug=debug)
        
        # Share instances with receiver
        self.receiver.buffers = self.buffers
        self.receiver.processors = self.processors
        
        # Register callbacks for each symbol
        for symbol in symbols:
            self.receiver.register_callback(symbol, self._make_callback(symbol))

        # Capture transitions for status reporting
        self.transition_log: List[StateTransition] = []
        self.dashboard = None

    @property
    def mt5_symbol(self) -> str:
        return self.symbols[0] if self.symbols else "EURUSDm"

    @property
    def yf_symbol(self) -> str:
        sym = self.mt5_symbol.replace("m", "").replace(".m", "").upper()
        return f"{sym}=X"

    @property
    def tick_engine(self):
        class TickEngineProxy:
            def __init__(self, receiver):
                self.receiver = receiver
            @property
            def latest_bid(self):
                symbol = list(self.receiver.buffers.keys())[0] if self.receiver.buffers else "EURUSDm"
                snap = self.receiver.buffers[symbol].snapshot()
                return snap[-1].bid if snap else 1.1600
            @property
            def latest_ask(self):
                symbol = list(self.receiver.buffers.keys())[0] if self.receiver.buffers else "EURUSDm"
                snap = self.receiver.buffers[symbol].snapshot()
                return snap[-1].ask if snap else 1.1605
            @property
            def spread(self):
                return self.latest_ask - self.latest_bid
            @property
            def _tick_count(self):
                symbol = list(self.receiver.buffers.keys())[0] if self.receiver.buffers else "EURUSDm"
                return self.receiver.tick_counts.get(symbol, 0)
        return TickEngineProxy(self.receiver)

    @property
    def live_state(self):
        class LiveStateProxy:
            def __init__(self, orchestrator):
                self.orchestrator = orchestrator
            @property
            def current_price(self):
                symbol = self.orchestrator.symbols[0]
                snap = self.orchestrator.buffers[symbol].snapshot()
                return snap[-1].mid if snap else 1.1600
        return LiveStateProxy(self)

    def _get_candles_payload(self, timeframe: str) -> dict:
        tf_map = {
            "M15": mt5.TIMEFRAME_M15 if hasattr(mt5, "TIMEFRAME_M15") else 15,
            "H1": mt5.TIMEFRAME_H1 if hasattr(mt5, "TIMEFRAME_H1") else 16385,
            "H4": mt5.TIMEFRAME_H4 if hasattr(mt5, "TIMEFRAME_H4") else 16388
        }
        tf_val = tf_map.get(timeframe, 15)
        rates = mt5.copy_rates_from_pos(self.mt5_symbol, tf_val, 0, 200)
        candles_list = []
        if rates is not None:
            for r in rates:
                candles_list.append({
                    "time": int(r['time']),
                    "open": float(r['open']),
                    "high": float(r['high']),
                    "low": float(r['low']),
                    "close": float(r['close'])
                })
        return {
            "type": "candles",
            "symbol": self.mt5_symbol,
            "timeframe": timeframe,
            "candles": candles_list
        }

    def _get_levels_payload(self) -> dict:
        # Simple dynamic support/resistance zones
        symbol = self.mt5_symbol
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 50)
        levels = []
        if rates is not None:
            highs = [float(r['high']) for r in rates]
            lows = [float(r['low']) for r in rates]
            levels.append({
                "price": max(highs),
                "level_type": "PDH",
                "direction": "resistance",
                "strength": 0.85,
                "touches": 3,
                "timeframe": "H1"
            })
            levels.append({
                "price": min(lows),
                "level_type": "PDL",
                "direction": "support",
                "strength": 0.90,
                "touches": 4,
                "timeframe": "H1"
            })
        return {
            "type": "levels",
            "symbol": symbol,
            "price_levels": levels
        }

    def _get_account_payload(self) -> Optional[dict]:
        acc = mt5.account_info()
        if not acc:
            return None
        
        positions = mt5.positions_get(symbol=self.mt5_symbol)
        pos_list = []
        if positions:
            for p in positions:
                pos_list.append({
                    "ticket": int(p.ticket),
                    "symbol": p.symbol,
                    "type": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                    "volume": float(p.volume),
                    "price_open": float(p.price_open),
                    "price_current": float(p.price_current),
                    "sl": float(p.sl),
                    "tp": float(p.tp),
                    "profit": float(p.profit)
                })
        
        return {
            "type": "account",
            "balance": acc.balance,
            "equity": acc.equity,
            "profit": acc.profit,
            "margin": acc.margin,
            "free_margin": acc.margin_free,
            "margin_level": acc.margin_level if hasattr(acc, "margin_level") else 0.0,
            "positions": pos_list
        }

    def _get_regime_payload(self, symbol: str) -> dict:
        sm = self.state_machines[symbol]
        snap = self.buffers[symbol].snapshot()
        mid_price = snap[-1].mid if snap else 1.1600
        spread_pips = (snap[-1].spread / 0.0001) if snap else 1.0
        
        # Session determination
        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc)
        utc_hour = now_utc.hour + now_utc.minute / 60.0
        
        session = "asian"
        if 8.0 <= utc_hour < 16.0:
            session = "london"
        elif 13.0 <= utc_hour < 21.0:
            session = "newyork"
            
        session_details = [
            {"name": "Sydney", "active": 22.0 <= utc_hour or utc_hour < 7.0, "progress": 0.5, "remaining_min": 180, "color": "#00bfff", "open_utc": 22.0, "close_utc": 7.0},
            {"name": "Tokyo", "active": 0.0 <= utc_hour < 9.0, "progress": 0.3, "remaining_min": 240, "color": "#ff6b9d", "open_utc": 0.0, "close_utc": 9.0},
            {"name": "London", "active": 8.0 <= utc_hour < 16.0, "progress": 0.6, "remaining_min": 120, "color": "#9d00ff", "open_utc": 8.0, "close_utc": 16.0},
            {"name": "New York", "active": 13.0 <= utc_hour < 22.0, "progress": 0.2, "remaining_min": 360, "color": "#00ff66", "open_utc": 13.0, "close_utc": 22.0}
        ]

        buy_p = 0.5
        sell_p = 0.5
        if snap:
            buy_p = snap[-1].mid / (snap[-1].mid + 1.0)
            sell_p = 1.0 / (snap[-1].mid + 1.0)

        # Fetch dynamic tick indicators from the processor
        processor = self.processors[symbol]
        last_state = processor.last_signal_state
        
        tick_velocity = last_state.velocity if last_state else 0.0
        tick_imbalance = last_state.imbalance if last_state else 1.0
        tick_spread_delta = last_state.spread_delta if last_state else 0.0
        tick_collapse = last_state.velocity_collapse if last_state else False
        tick_agg_shift = last_state.aggression_shift if last_state else False
        tick_absorption = last_state.absorption if last_state else False

        return {
            "type": "regime",
            "symbol": symbol,
            "dominant": sm.current_state.name,
            "confidence": sm.state_probability,
            "volatility": "normal",
            "atr": 0.00015,
            "spread_pips": spread_pips,
            "spread_safe": spread_pips < 2.0,
            "belief": sm.state_probability,
            "should_run_graph": True,
            "abort_reason": "",
            "session": session,
            "session_quality": "normal",
            "session_details": session_details,
            "market_closed": False,
            "market_resume_timestamp": 0,
            "daemon_start_time": self._start_time * 1000,
            "cooldown_remaining": 0,
            "events_detected": len(self.transition_log),
            "events_fired": len(self.execution.trades),
            "events_skipped": 0,
            "regime_scores": {s.name: 0.8 if s == sm.current_state else 0.1 for s in sm.current_state.__class__},
            "eur_strength": buy_p * 10.0,
            "usd_strength": sell_p * 10.0,
            "hours_since_london_open": 2.5,
            "trend_h4": "up",
            "trend_h1": "up",
            "trend_m15": "up",
            "tokens_in": 0,
            "tokens_out": 0,
            "tokens_total": 0,
            "llm_calls": 0,
            "tool_calls": 0,
            # --- New Tick Microstructure indicators ---
            "tick_velocity": tick_velocity,
            "tick_imbalance": tick_imbalance,
            "tick_spread_delta": tick_spread_delta,
            "tick_collapse": tick_collapse,
            "tick_agg_shift": tick_agg_shift,
            "tick_absorption": tick_absorption
        }

    def _make_callback(self, symbol: str):
        """Helper to create independent callback scopes per symbol."""
        def callback(signal_state: SignalState):
            self._on_signal(symbol, signal_state)
        return callback

    def _on_signal(self, symbol: str, signal: SignalState):
        """Main synchronous event loop triggered on every received tick."""
        # Resolve symbol variations (e.g. 'EURUSD' -> 'EURUSDm')
        if symbol not in self.symbols and symbol not in self.state_machines:
            for s in self.symbols:
                if s.startswith(symbol) or symbol.startswith(s):
                    symbol = s
                    break

        # Broadcast raw tick to dashboard
        if self.dashboard:
            self.dashboard.broadcast({
                "type": "tick",
                "symbol": symbol,
                "bid": signal.bid,
                "ask": signal.ask,
                "spread": signal.spread_pips,
                "time": int(time.time()),
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            })
            
            # Broadcast regime and account data on interval
            if self.receiver.tick_counts[symbol] % 5 == 1:
                self.dashboard.broadcast(self._get_regime_payload(symbol))
                acc = self._get_account_payload()
                if acc:
                    self.dashboard.broadcast(acc)

        # Update State Machine
        transition = self.state_machines[symbol].update(signal)
        
        if transition:
            self.transition_log.append(transition)
            context = self.context_builder.build(symbol, signal, self.state_machines[symbol])
            
            # Broadcast state transition event to WebUI
            if self.dashboard:
                self.dashboard.broadcast({
                    "type": "event",
                    "id": len(self.transition_log),
                    "event_type": "regime_shift",
                    "priority": "HIGH",
                    "price": signal.mid,
                    "details": {"from_state": transition.from_state, "to_state": transition.to_state},
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "firing",
                    "events_detected": len(self.transition_log),
                    "events_fired": len(self.execution.trades),
                    "events_skipped": 0
                })
                
                # Active agent working panel broadcast
                self.dashboard.broadcast({
                    "type": "agent",
                    "agent_name": "WYCKOFF",
                    "status": "active",
                    "message": f"Analyzing market state transition from {transition.from_state} to {transition.to_state}...",
                    "tool_calls": [],
                    "timestamp": time.strftime("%H:%M:%S")
                })
            
            if self.llm.should_query(transition, context):
                logger.info("High-value transition detected for %s. Querying LLM...", symbol)
                
                if self.dashboard:
                    self.dashboard.broadcast({
                        "type": "agent",
                        "agent_name": "DRUCKENMILLER",
                        "status": "active",
                        "message": "Assessing strategic liquidity risk and microstructure trade criteria...",
                        "tool_calls": ["claude_query"],
                        "timestamp": time.strftime("%H:%M:%S")
                    })
                
                decision = self.llm.query(transition, context)
                
                if self.dashboard:
                    self.dashboard.broadcast({
                        "type": "agent",
                        "agent_name": "DRUCKENMILLER",
                        "status": "completed",
                        "message": f"Strategic Analysis: {decision.reasoning}\nMax Risk Pips: {decision.max_risk_pips}",
                        "tool_calls": [],
                        "timestamp": time.strftime("%H:%M:%S")
                    })
                    
                # Execute order
                record = self.execution.evaluate(decision, context)
                if record and record.executed:
                    if self.dashboard:
                        self.dashboard.broadcast({
                            "type": "decision",
                            "signal": "BUY" if decision.action == "long" else "SELL",
                            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                        })
                        acc = self._get_account_payload()
                        if acc:
                            self.dashboard.broadcast(acc)

    def start(self):
        """Start the live trading orchestrator and the WebSocket dashboard server."""
        logger.info("Starting TradingOrchestrator...")
        self.receiver.start()
        
        # Align symbol lists in case receiver resolved suffixes
        resolved_symbols = self.receiver.symbols
        if resolved_symbols != self.symbols:
            logger.info("Updating orchestrator symbols to resolved versions: %s", resolved_symbols)
            self.symbols = resolved_symbols
            self.buffers = self.receiver.buffers
            self.processors = self.receiver.processors
            
            # Map state machines to resolved counterparts
            new_sms = {}
            for original, resolved in zip(["EURUSD", "GBPUSD"], resolved_symbols):
                new_sms[resolved] = self.state_machines.get(original, MarketStateMachine(debug=self.debug))
            self.state_machines = new_sms

        # Initialize and start WebUI API dashboard server
        try:
            from axonai.realtime.api_server import start_dashboard
            self.dashboard = start_dashboard(host="127.0.0.1", port=8000)
            self.dashboard.daemon = self
            
            # Send initial hydrate payloads
            self.dashboard.broadcast(self._get_levels_payload())
            self.dashboard.broadcast(self._get_regime_payload(self.symbols[0]))
            self.dashboard.broadcast(self._get_candles_payload("M15"))
            self.dashboard.broadcast(self._get_candles_payload("H1"))
            acc = self._get_account_payload()
            if acc:
                self.dashboard.broadcast(acc)
            
            logger.info("WebUI Dashboard Server started successfully at http://127.0.0.1:8000")
        except Exception as e:
            logger.warning("Could not start WebUI Dashboard Server: %s", e)

    def stop(self):
        """Clean shutdown of all orchestrator systems."""
        logger.info("Stopping TradingOrchestrator...")
        self.receiver.stop()

    def status(self) -> dict:
        """Returns the current runtime status report."""
        return {
            "symbols": self.symbols,
            "tick_counts": self.receiver.tick_counts,
            "current_states": {s: self.state_machines[s].current_state.name for s in self.symbols},
            "transition_count": len(self.transition_log),
            "llm_query_count": len([t for t in self.execution.trades if "LLM" not in (t.rejection_reason or "")]),
            "trades_attempted": len(self.execution.trades),
            "trades_executed": len([t for t in self.execution.trades if t.executed]),
            "positions_count": len(self.execution.trades)
        }

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    api_key = os.getenv("ANTHROPIC_API_KEY", "mock_key")
    
    print("Initializing system for live WebUI Telemetry run...")
    orchestrator = TradingOrchestrator(api_key=api_key, dry_run=True, debug=True)
    orchestrator.start()
    
    try:
        start_time = time.time()
        # Keep running
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("Interrupt detected. Shutting down...")
    finally:
        orchestrator.stop()
        print("System stopped clean.")
