"""Mock daemon: generates simulated forex data for dashboard UI testing.

Usage:
    python _mock_daemon.py

Starts the dashboard at http://127.0.0.1:8000 and broadcasts simulated
tick/regime/account/candles/levels data so the UI can be tested visually
without a MetaTrader 5 connection.
"""

import sys
import time
import math
import random
import threading
from datetime import datetime, timedelta

sys.path.insert(0, ".")

from axonai.realtime.api_server import start_dashboard


# ── Simulated forex state ──────────────────────────────────────────
class MockState:
    def __init__(self):
        self.bid = 1.08500
        self.ask = 1.08523
        self.spread_pips = 2.3
        self.balance = 10427.35
        self.equity = 10482.10
        self.profit = 54.75
        self.margin = 320.00
        self.free_margin = 10162.10
        self.margin_level = 3275.66
        self.regime = "TRENDING"
        self.volatility = "MODERATE"
        self.atr = 0.0032
        self.belief = 0.65
        self.dominant_trend = "up"
        self.hour = 8
        self.tick_count = 0

    def advance(self):
        """Advance state by one tick (~100ms)"""
        self.tick_count += 1
        drift = 0.00001 * (0.6 if self.dominant_trend == "up" else -0.4)
        noise = random.gauss(0, 0.00008)
        change = drift + noise
        self.bid += change
        self.ask = self.bid + self.spread_pips * 0.00001
        self.equity = self.balance + self.profit + random.gauss(0, 2)
        self.profit += change * 1000 + random.gauss(0, 0.5)

        # Cycle regime periodically
        if self.tick_count % 500 == 0:
            self.regime = random.choice(["TRENDING", "RANGING", "VOLATILE", "SIDEWAYS"])
            self.dominant_trend = random.choice(["up", "down", "sideways"])
        if self.tick_count % 300 == 0:
            self.belief = max(-1, min(1, self.belief + random.uniform(-0.3, 0.3)))


def get_tick(state):
    return {
        "type": "tick",
        "symbol": "EURUSDm",
        "bid": round(state.bid, 5),
        "ask": round(state.ask, 5),
        "spread": state.spread_pips,
        "time": int(time.time()),
        "tick_velocity": round(random.uniform(-3, 3), 2),
        "tick_imbalance_10s": round(random.uniform(-1, 1), 2),
        "tick_imbalance_60s": round(random.uniform(-1, 1), 2),
        "tick_imbalance_300s": round(random.uniform(-1, 1), 2),
        "tick_spread_delta": round(random.uniform(-0.5, 0.5), 2),
        "tick_collapse": random.random() < 0.02,
        "tick_agg_shift": random.random() < 0.01,
        "tick_absorption": random.random() < 0.015,
    }


def get_regime(state):
    return {
        "type": "regime",
        "symbol": "EURUSDm",
        "dominant": state.regime,
        "confidence": round(random.uniform(0.6, 0.95), 2),
        "volatility": state.volatility,
        "atr": state.atr,
        "spread_pips": state.spread_pips,
        "spread_safe": state.spread_pips < 3.0,
        "belief": round(state.belief, 2),
        "market_closed": False,
        "cooldown_remaining": max(0, 300 - state.tick_count % 300),
        "events_detected": random.randint(10, 50),
        "events_fired": random.randint(1, 8),
        "events_skipped": random.randint(5, 30),
        "trend_h4": random.choice(["up", "down", "sideways"]),
        "trend_h1": random.choice(["up", "down", "sideways"]),
        "trend_m15": state.dominant_trend,
        "london_open_bias": random.choice(["BULLISH", "BEARISH", "NEUTRAL"]),
        "london_range_high": round(state.bid + 0.002, 5),
        "london_range_low": round(state.bid - 0.002, 5),
        "eur_strength": round(random.uniform(-3, 3), 1),
        "usd_strength": round(random.uniform(-3, 3), 1),
        "tokens_in": random.randint(500, 2000),
        "tokens_out": random.randint(200, 1500),
        "tokens_total": random.randint(1000, 4000),
        "llm_calls": random.randint(1, 5),
        "tool_calls": random.randint(3, 15),
    }


def get_account(state):
    return {
        "type": "account",
        "balance": round(state.balance, 2),
        "equity": round(state.equity, 2),
        "profit": round(state.profit, 2),
        "margin": round(state.margin, 2),
        "free_margin": round(state.free_margin, 2),
        "margin_level": round(state.margin_level, 2),
        "positions": [
            {
                "ticket": 12345678,
                "symbol": "EURUSDm",
                "type": "buy",
                "volume": 0.01,
                "price_open": round(state.bid - 0.001, 5),
                "price_current": round(state.bid, 5),
                "sl": round(state.bid - 0.005, 5),
                "tp": round(state.bid + 0.005, 5),
                "profit": round(state.profit, 2),
            }
        ],
    }


def get_candles():
    now = int(time.time())
    base = now - 100 * 60
    candles = []
    price = 1.08500
    for i in range(100):
        t = base + i * 60
        o = price
        c = o + random.gauss(0, 0.0002)
        h = max(o, c) + random.uniform(0, 0.0003)
        l = min(o, c) - random.uniform(0, 0.0003)
        candles.append({
            "time": t,
            "open": round(o, 5),
            "high": round(h, 5),
            "low": round(l, 5),
            "close": round(c, 5),
        })
        price = c
    return {"type": "candles", "timeframe": "M15", "candles": candles}


def get_levels(state):
    levels = []
    for name, direction, offset in [
        ("H4_SWING", "resistance", 0.003),
        ("PDH", "resistance", 0.002),
        ("H4_SWING", "support", -0.003),
        ("PDL", "support", -0.002),
    ]:
        levels.append({
            "price": round(state.bid + offset, 5),
            "level_type": name,
            "direction": direction,
            "strength": round(random.uniform(0.3, 1.0), 2),
            "touches": random.randint(1, 8),
            "timeframe": "H1",
        })
    return {"type": "levels", "price_levels": levels}


def mock_broadcast(server, state):
    """Periodically push mock data through the dashboard server."""
    tick_interval = 0.1        # ~10 ticks/sec
    slow_interval = 2.0        # regime/candles every 2s
    last_slow = 0.0
    last_account = 0.0
    last_candles = 0.0
    last_levels = 0.0
    last_event = 0.0

    events = [
        ("LEVEL_BREACH", "HIGH"),
        ("STRUCTURE_BREAK", "MEDIUM"),
        ("SWEEP_DETECTED", "HIGH"),
        ("VOLATILITY_SPIKE", "MEDIUM"),
        ("CANDLE_PATTERN", "LOW"),
        ("REGIME_SHIFT", "HIGH"),
        ("MOMENTUM_DIVERGENCE", "MEDIUM"),
    ]
    event_idx = 0

    while True:
        state.advance()
        now = time.time()

        # Fast: tick every 100ms
        server.broadcast(get_tick(state))

        # Slow: regime/trend data
        if now - last_slow >= slow_interval:
            server.broadcast(get_regime(state))
            last_slow = now

        # Account
        if now - last_account >= 5.0:
            server.broadcast(get_account(state))
            last_account = now

        # Candles
        if now - last_candles >= 15.0:
            server.broadcast(get_candles())
            last_candles = now

        # Levels
        if now - last_levels >= 10.0:
            server.broadcast(get_levels(state))
            last_levels = now

        # Events
        if now - last_event >= 12.0:
            et, prio = events[event_idx % len(events)]
            event_idx += 1
            server.broadcast({
                "type": "event",
                "id": event_idx + 1000,
                "event_type": et,
                "priority": prio,
                "price": round(state.bid, 5),
                "details": {"reason": f"Simulated {et} at {state.bid:.5f}"},
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "firing",
            })
            last_event = now

        time.sleep(tick_interval)


def main():
    print("=" * 60)
    print("  AxonAI Mock Daemon — Dashboard UI Testing Tool")
    print("  Generates simulated forex data for visual testing")
    print("=" * 60)
    print()

    # Start the real dashboard server
    server = start_dashboard(host="127.0.0.1", port=8000)
    print(f"  Dashboard: http://127.0.0.1:8000")
    print(f"  WebSocket: ws://127.0.0.1:8000/ws")
    print()
    print("  Broadcasting simulated EURUSD data...")
    print("  Press Ctrl+C to stop.")
    print()

    state = MockState()

    # Push initial data immediately
    server.broadcast(get_tick(state))
    server.broadcast(get_regime(state))
    server.broadcast(get_account(state))
    server.broadcast(get_candles())
    server.broadcast(get_levels(state))

    # Run mock broadcasting in a background thread
    t = threading.Thread(target=mock_broadcast, args=(server, state), daemon=True)
    t.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Mock daemon stopped.")


if __name__ == "__main__":
    main()
