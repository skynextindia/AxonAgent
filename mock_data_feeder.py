#!/usr/bin/env python3
"""Mock Data Feeder for AxonAI Dashboard.

Simulates live market data (ticks, levels, regime, account, events, agents,
decisions) and broadcasts it through the DashboardServer WebSocket so the
dashboard UI comes alive — including LevelBehaviorTracker metrics.
"""

import time
import math
import random
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [MOCK] %(message)s")
log = logging.getLogger("MockFeeder")

# ── EUR/USD parameters ──────────────────────────────────────────────
BASE_PRICE = 1.10500
PIP = 0.0001
SPREAD = 1.2 * PIP

# Support & Resistance levels the price will interact with
LEVELS = [
    {"price": 1.10950, "type": "H4_SWING", "dir": "resistance", "strength": 0.90},
    {"price": 1.10850, "type": "PWH",      "dir": "resistance", "strength": 0.85},
    {"price": 1.10700, "type": "ROUND",    "dir": "resistance", "strength": 0.60},
    {"price": 1.10500, "type": "PDH",      "dir": "resistance", "strength": 0.50},
    {"price": 1.10350, "type": "H4_SWING", "dir": "support",    "strength": 0.88},
    {"price": 1.10200, "type": "PDL",      "dir": "support",    "strength": 0.65},
    {"price": 1.10050, "type": "PWL",      "dir": "support",    "strength": 0.55},
    {"price": 1.09900, "type": "H4_SWING", "dir": "support",    "strength": 0.75},
    {"price": 1.09750, "type": "PWH",      "dir": "support",    "strength": 0.70},
]

MID_PRICE = (
    max(l["price"] for l in LEVELS if l["dir"] == "resistance") +
    min(l["price"] for l in LEVELS if l["dir"] == "support")
) / 2  # = (1.10950 + 1.09750) / 2 = 1.10350

# Behavior tracker state for each level
behavior_state = {
    str(lv["price"]): {
        "total_attacks": 0,
        "consecutive_attacks": 0,
        "rejection_count": 0,
        "ticks_in_zone": 0,
        "absorption_ratio": 0.0,
        "last_rejection_velocity": 0.0,
        "avg_rejection_velocity": 0.0,
        "imbalance": 0.0,
        "is_absorbing": False,
        "attack_quality": "none",
        "status": "fresh",
        "has_pullback": False,
        "approach_duration_ms": 0,
    }
    for lv in LEVELS
}

# ── Phase cycle ─────────────────────────────────────────────────────
# Each phase lasts ~15-25 seconds, then rotates
PHASES = ["approached", "weakening", "pressured", "breakout", "fresh"]
current_phase_idx = 0
phase_tick_counter = 0
PHASE_DURATION_TICKS = 25  # ticks per phase

# Start price between support and resistance
current_price = 1.10400
drift = 0.0  # current price drift direction


def pick_target_level():
    """Pick the nearest level (resistance above or support below) for the price to interact with.
    If price is outside all levels, returns the outermost level and the next one inward."""
    global current_price
    res_above = [l for l in LEVELS if l["dir"] == "resistance" and l["price"] > current_price]
    sup_below = [l for l in LEVELS if l["dir"] == "support" and l["price"] < current_price]

    nearest_res = min(res_above, key=lambda l: l["price"]) if res_above else None
    nearest_sup = max(sup_below, key=lambda l: l["price"]) if sup_below else None

    # If price is above all resistance, target the highest resistance as the "ceiling"
    if nearest_res is None:
        all_res = [l for l in LEVELS if l["dir"] == "resistance"]
        if all_res:
            nearest_res = max(all_res, key=lambda l: l["price"])

    # If price is below all support, target the lowest support as the "floor"
    if nearest_sup is None:
        all_sup = [l for l in LEVELS if l["dir"] == "support"]
        if all_sup:
            nearest_sup = min(all_sup, key=lambda l: l["price"])

    return nearest_res, nearest_sup


def simulate_tick():
    """Generate the next tick price based on current phase and levels.
    Price oscillates between support/resistance, cycling through behavior phases."""
    global current_price, drift, current_phase_idx, phase_tick_counter

    res, sup = pick_target_level()
    phase = PHASES[current_phase_idx]

    # Base random walk noise
    noise = random.gauss(0, 0.15 * PIP)

    # Determine a target price based on phase — always stay between nearest sup and res
    target_price = current_price  # default: no change

    if phase == "fresh":
        # Wander toward the middle
        target_price = MID_PRICE
        drift_target = (target_price - current_price) * 0.04
        drift = drift * 0.9 + drift_target + random.gauss(0, 0.1 * PIP)

    elif phase == "approached":
        # Drift up toward nearest resistance (but stay slightly below it)
        if res:
            target_price = res["price"] - 0.3 * PIP
        else:
            target_price = MID_PRICE
        drift_target = (target_price - current_price) * 0.08
        drift = drift * 0.8 + drift_target

    elif phase == "weakening":
        # Tap resistance and bounce back with decreasing velocity
        if res:
            target_price = res["price"] - 0.1 * PIP
            # Bounce away if very close
            if current_price >= res["price"] - 0.3 * PIP:
                bounce = -abs(drift) * 0.6 if drift > 0 else 0
                noise += bounce
        else:
            target_price = MID_PRICE
        drift_target = (target_price - current_price) * 0.12
        drift = drift * 0.7 + drift_target

    elif phase == "pressured":
        # Push harder against resistance, some attempts break through
        if res:
            target_price = res["price"] + 0.1 * PIP
            if current_price >= res["price"] - 0.2 * PIP:
                if random.random() < 0.3:
                    noise += abs(noise) * 2.0  # push through
                else:
                    noise += abs(noise) * -1.0  # bounce
        else:
            target_price = MID_PRICE
        drift_target = (target_price - current_price) * 0.15
        drift = drift * 0.6 + drift_target

    elif phase == "breakout":
        # Spike above resistance then mean-revert
        if res:
            target_price = res["price"] + 2.0 * PIP
        else:
            target_price = MID_PRICE
        drift_target = (target_price - current_price) * 0.2
        drift = drift * 0.5 + drift_target

    current_price += drift + noise
    phase_tick_counter += 1

    # Rotate phase every N ticks; on "fresh" phase, pull price firmly to mid-range
    if phase_tick_counter >= PHASE_DURATION_TICKS:
        current_phase_idx = (current_phase_idx + 1) % len(PHASES)
        phase_tick_counter = 0
        if PHASES[current_phase_idx] == "fresh":
            # Strong pull to mid price
            current_price = current_price * 0.3 + MID_PRICE * 0.7
            drift = 0.0

    # Clamp firmly inside the level band so pick_target_level never fails
    res_ceiling = max(l["price"] for l in LEVELS if l["dir"] == "resistance")
    sup_floor = min(l["price"] for l in LEVELS if l["dir"] == "support")
    current_price = max(min(current_price, res_ceiling + 0.2 * PIP), sup_floor - 0.2 * PIP)

    return current_price


def update_behavior_metrics():
    """Update behavior tracker state for all levels based on price proximity."""
    global current_price

    for lv in LEVELS:
        price_key = str(lv["price"])
        bhv = behavior_state[price_key]
        dist = abs(current_price - lv["price"])
        phase = PHASES[current_phase_idx]
        tick_vel = abs(drift) / PIP if PIP != 0 else 0

        # Determine if price is interacting with this level
        attacking = dist < 1.5 * PIP
        very_close = dist < 0.5 * PIP
        rejecting = very_close and (
            (lv["dir"] == "resistance" and drift < -0.1 * PIP) or
            (lv["dir"] == "support" and drift > 0.1 * PIP)
        )

        if attacking:
            bhv["total_attacks"] += 1
            bhv["consecutive_attacks"] += 1
            bhv["ticks_in_zone"] += 1

            # Update absorption ratio
            if bhv["consecutive_attacks"] > 3:
                bhv["absorption_ratio"] = min(100, bhv["absorption_ratio"] + random.uniform(2, 8))
                bhv["is_absorbing"] = bhv["absorption_ratio"] > 30
            else:
                bhv["absorption_ratio"] = max(0, bhv["absorption_ratio"] - random.uniform(0, 3))

            # Update imbalance
            bhv["imbalance"] = max(-1, min(1, bhv["imbalance"] + random.gauss(0, 0.05)))

            if rejecting:
                bhv["rejection_count"] += 1
                rej_vel = tick_vel * random.uniform(0.8, 1.5)
                bhv["last_rejection_velocity"] = round(rej_vel, 2)
                if bhv["rejection_count"] > 1:
                    old_avg = bhv["avg_rejection_velocity"]
                    bhv["avg_rejection_velocity"] = round(
                        (old_avg * (bhv["rejection_count"] - 1) + rej_vel) / bhv["rejection_count"], 2
                    )
                else:
                    bhv["avg_rejection_velocity"] = round(rej_vel, 2)
        else:
            bhv["consecutive_attacks"] = 0
            if bhv["absorption_ratio"] > 0:
                bhv["absorption_ratio"] = max(0, bhv["absorption_ratio"] - random.uniform(0.5, 2))
            bhv["is_absorbing"] = bhv["absorption_ratio"] > 30

        # Determine attack quality and status based on phase and this level's role
        if not attacking:
            bhv["attack_quality"] = "none"
            bhv["status"] = "fresh" if dist > 3 * PIP else "approaching"
        else:
            if phase == "approached":
                bhv["attack_quality"] = "approached"
                bhv["status"] = "testing"
            elif phase == "weakening":
                bhv["attack_quality"] = "weakening"
                bhv["status"] = "testing"
            elif phase == "pressured":
                bhv["attack_quality"] = "pressured"
                bhv["status"] = "holding"
            elif phase == "breakout":
                bhv["attack_quality"] = "breakout" if very_close else "pressured"
                bhv["status"] = "breaking" if very_close else "consolidating"
            else:
                bhv["attack_quality"] = "none"
                bhv["status"] = "fresh"


def build_tick_payload(bid_price):
    """Build a tick message matching daemon's _on_tick format."""
    ask_price = bid_price + SPREAD
    velocity = abs(drift) / PIP if PIP != 0 else 0
    return {
        "type": "tick",
        "symbol": "EURUSDm",
        "bid": round(bid_price, 5),
        "ask": round(ask_price, 5),
        "spread": round(SPREAD / PIP, 1),
        "time": int(time.time()),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "tick_velocity": round(velocity, 2),
        "tick_imbalance_10s": random.choice([-0.6, -0.3, 0.0, 0.3, 0.6]),
        "tick_imbalance_60s": random.choice([-0.4, -0.2, 0.1, 0.4, 0.5]),
        "tick_imbalance_300s": random.uniform(-0.3, 0.3),
        "tick_spread_delta": round(random.gauss(0, 0.00002), 5),
        "tick_collapse": random.random() < 0.02,
        "tick_agg_shift": random.random() < 0.08,
        "tick_absorption": random.random() < 0.05,
    }


def build_levels_payload():
    """Build a levels message matching daemon's _get_levels_payload format,
    enriched with LevelBehaviorTracker metrics."""
    price_levels = []
    for lv in LEVELS:
        price_key = str(lv["price"])
        bhv = behavior_state[price_key]
        entry = {
            "price": lv["price"],
            "level_type": lv["type"],
            "direction": lv["dir"],
            "strength": lv["strength"],
            "touches": bhv["total_attacks"],
            "timeframe": "H1",
        }
        if bhv["total_attacks"] > 0:
            entry.update({
                "total_attacks": bhv["total_attacks"],
                "consecutive_attacks": bhv["consecutive_attacks"],
                "rejection_count": bhv["rejection_count"],
                "last_rejection_velocity": bhv["last_rejection_velocity"],
                "avg_rejection_velocity": bhv["avg_rejection_velocity"],
                "absorption_ratio": round(bhv["absorption_ratio"], 1),
                "imbalance": round(bhv["imbalance"], 3),
                "is_absorbing": bhv["is_absorbing"],
                "attack_quality": bhv["attack_quality"],
                "status": bhv["status"],
            })
        price_levels.append(entry)

    return {"type": "levels", "price_levels": price_levels}


def build_regime_payload():
    """Build a regime message."""
    phase = PHASES[current_phase_idx]
    if phase == "breakout":
        dom, conf = "bearish", 0.75
    elif phase == "pressured":
        dom, conf = "bearish", 0.60
    elif phase == "weakening":
        dom, conf = "neutral", 0.50
    elif phase == "approached":
        dom, conf = "bullish", 0.55
    else:
        dom, conf = "neutral", 0.40

    now = datetime.now()
    utc_h = now.hour

    # Session detection
    sessions = [
        {"name": "Sydney",  "active": 21 <= utc_h or utc_h < 6,  "open_utc": 21, "close_utc": 6,  "color": "#1a6b3c"},
        {"name": "Tokyo",   "active": 23 <= utc_h or utc_h < 8,  "open_utc": 23, "close_utc": 8,  "color": "#8b4513"},
        {"name": "London",  "active": 7 <= utc_h < 16,            "open_utc": 7,  "close_utc": 16, "color": "#1a3a6b"},
        {"name": "New York","active": 12 <= utc_h < 21,           "open_utc": 12, "close_utc": 21, "color": "#6b1a3a"},
    ]
    for s in sessions:
        if s["active"]:
            elapsed = (utc_h - s["open_utc"]) % 24
            dur = (s["close_utc"] - s["open_utc"]) % 24
            if dur <= 0:
                dur += 24
            s["progress"] = round(min(1.0, elapsed / dur), 2)
            s["remaining_min"] = int((dur - elapsed) * 60)
        else:
            s["progress"] = 0
            s["remaining_min"] = 0

    return {
        "type": "regime",
        "symbol": "EURUSDm",
        "dominant": dom,
        "confidence": conf,
        "volatility": random.choice(["low", "normal", "high"]),
        "atr": round(random.uniform(0.0010, 0.0025), 5),
        "spread_pips": round(SPREAD / PIP, 1),
        "spread_safe": True,
        "belief": round(random.uniform(0.3, 0.8), 2),
        "should_run_graph": random.random() < 0.05,
        "abort_reason": None,
        "session": next((s["name"].lower() for s in sessions if s["active"]), "off"),
        "session_quality": random.choice([None, "high", "medium", "low"]),
        "session_details": sessions,
        "market_closed": False,
        "market_resume_timestamp": int(time.time()) + 3600,
        "daemon_start_time": time.time() * 1000,
        "cooldown_remaining": random.randint(0, 300),
        "events_detected": random.randint(10, 50),
        "events_fired": random.randint(1, 8),
        "events_skipped": random.randint(2, 15),
        "regime_scores": {"bullish": 0.3, "bearish": 0.5, "sideways": 0.2},
        "eur_strength": round(random.uniform(-3, 3), 2),
        "usd_strength": round(random.uniform(-3, 3), 2),
        "hours_since_london_open": round(random.uniform(0, 9), 1),
        "trend_h4": random.choice(["up", "down", "sideways"]),
        "trend_h1": random.choice(["up", "down", "sideways"]),
        "trend_m15": random.choice(["up", "down", "sideways"]),
        "rsi_h1": round(random.uniform(30, 70), 1),
        "macd_signal_h1": random.choice(["bullish", "bearish", None]),
        "london_open_bias": random.choice([None, "bullish", "bearish"]),
        "asian_range_high": round(BASE_PRICE + 0.0020, 5),
        "asian_range_low": round(BASE_PRICE - 0.0015, 5),
        "london_range_high": round(BASE_PRICE + 0.0030, 5),
        "london_range_low": round(BASE_PRICE - 0.0025, 5),
        "ny_range_high": round(BASE_PRICE + 0.0040, 5),
        "ny_range_low": round(BASE_PRICE - 0.0035, 5),
        "tokens_in": random.randint(5000, 50000),
        "tokens_out": random.randint(1000, 10000),
        "tokens_total": random.randint(6000, 60000),
        "llm_calls": random.randint(1, 15),
        "tool_calls": random.randint(5, 40),
    }


def build_account_payload():
    """Build an account message."""
    return {
        "type": "account",
        "balance": 10000.00,
        "equity": round(10000 + random.gauss(0, 50), 2),
        "profit": round(random.gauss(0, 25), 2),
        "margin": round(200 + random.uniform(-10, 10), 2),
        "free_margin": round(9800 + random.gauss(0, 50), 2),
        "margin_level": round(random.uniform(2000, 5000), 1),
        "positions": [
            {
                "ticket": 1001,
                "symbol": "EURUSDm",
                "type": "BUY",
                "volume": 0.01,
                "price_open": 1.10450,
                "price_current": current_price,
                "sl": 1.09900,
                "tp": 1.11000,
                "profit": round((current_price - 1.10450) * 100000 * 0.01, 2),
            }
        ],
    }


def build_agent_payload():
    """Build an agent step message (occasional)."""
    agents = ["WYCKOFF", "KEYNES", "REUTERS", "LIVERMORE", "BUFFETT", "SOROS"]
    messages = [
        "Analyzing order flow at resistance level.",
        "Detecting absorption pattern on H1.",
        "Multiple rejections at 1.10500 - weakening structure.",
        "Bullish divergence on RSI - momentum fading.",
        "Volume profile shows low participation at highs.",
        "Swing rejection confirmed - anticipating reversal.",
        "Breakout attempt lacks follow-through.",
        "Institutional order flow suggests accumulation.",
    ]
    agent = random.choice(agents)
    return {
        "type": "agent",
        "agent_name": agent,
        "status": "active",
        "message": random.choice(messages),
        "tool_calls": [],
        "timestamp": datetime.now().strftime("%H:%M:%S"),
    }


def build_event_payload():
    """Build a market event message (occasional)."""
    prices = [l["price"] for l in LEVELS]
    event_types = ["price_attack", "level_test", "candle_close", "rejection"]
    et = random.choice(event_types)
    return {
        "type": "event",
        "id": random.randint(1, 999),
        "event_type": et,
        "priority": random.choice(["LOW", "MEDIUM", "HIGH"]),
        "price": round(random.choice(prices), 5),
        "details": f"Price testing {random.choice(['resistance', 'support'])} at ${random.choice(prices):.5f}",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": random.choice(["detected", "skipped", "firing"]),
        "events_detected": random.randint(10, 50),
        "events_fired": random.randint(1, 8),
        "events_skipped": random.randint(2, 15),
    }


def build_decision_payload():
    """Build a decision message (rare)."""
    signals = ["Hold", "Neutral", "Reduce Exposure", "Monitor Only"]
    return {
        "type": "decision",
        "signal": random.choice(signals),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def feeder_thread(dashboard, tick_interval=1.5):
    """Main feeder loop: runs in background, generates data, broadcasts."""
    log.info("Mock feeder thread started (interval=%.1fs)", tick_interval)
    tick_count = 0

    while True:
        try:
            # 1. Simulate next tick
            bid = simulate_tick()

            # 2. Update behavior metrics for all levels
            update_behavior_metrics()

            # 3. Broadcast tick (every tick)
            dashboard.broadcast(build_tick_payload(bid))

            # 4. Broadcast levels & regime every 3rd tick
            if tick_count % 3 == 0:
                dashboard.broadcast(build_levels_payload())
                dashboard.broadcast(build_regime_payload())

            # 5. Broadcast account every 5th tick
            if tick_count % 5 == 0:
                dashboard.broadcast(build_account_payload())

            # 6. Broadcast event every ~8th tick
            if tick_count % 8 == 0:
                dashboard.broadcast(build_event_payload())

            # 7. Broadcast agent every ~12th tick
            if tick_count % 12 == 0:
                dashboard.broadcast(build_agent_payload())

            # 8. Broadcast decision every ~30th tick
            if tick_count > 0 and tick_count % 30 == 0:
                dashboard.broadcast(build_decision_payload())

            tick_count += 1

            # Log phase transitions
            if tick_count % PHASE_DURATION_TICKS == 0:
                phase = PHASES[current_phase_idx]
                res, sup = pick_target_level()
                res_key = str(res["price"]) if res else "?"
                sup_key = str(sup["price"]) if sup else "?"
                log.info(
                    "Phase: %-12s Price: %.5f | RES: %.5f (ATK=%d, REJ=%d, Q=%s) | "
                    "SUP: %.5f (ATK=%d, REJ=%d, Q=%s)",
                    phase, current_price,
                    res["price"] if res else 0,
                    behavior_state.get(res_key, {}).get("total_attacks", 0),
                    behavior_state.get(res_key, {}).get("rejection_count", 0),
                    behavior_state.get(res_key, {}).get("attack_quality", "none"),
                    sup["price"] if sup else 0,
                    behavior_state.get(sup_key, {}).get("total_attacks", 0),
                    behavior_state.get(sup_key, {}).get("rejection_count", 0),
                    behavior_state.get(sup_key, {}).get("attack_quality", "none"),
                )

            time.sleep(tick_interval)

        except Exception as e:
            log.error("Feeder error: %s", e)
            time.sleep(1)


def main():
    """Start dashboard server + mock feeder."""
    from axonai.realtime.api_server import start_dashboard, get_dashboard

    log.info("Starting AxonAI Dashboard + Mock Data Feeder...")
    srv = start_dashboard(host="0.0.0.0", port=8000)

    # Wait for server to be ready
    time.sleep(2)

    # Start feeder in background thread
    feeder = threading.Thread(
        target=feeder_thread,
        args=(srv, 1.5),
        daemon=True,
        name="MockFeeder",
    )
    feeder.start()

    log.info("=" * 60)
    log.info("Dashboard: http://0.0.0.0:8000")
    log.info("Live data broadcasting with LevelBehavior metrics.")
    log.info("Price phases rotate: approached → weakening → pressured → breakout → fresh")
    log.info("=" * 60)

    # Keep main thread alive
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        log.info("Shutting down...")


if __name__ == "__main__":
    main()
