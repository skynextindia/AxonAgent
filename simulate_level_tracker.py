#!/usr/bin/env python3
"""Live simulation: verifies LevelBehaviorTracker with realistic tick data.

Simulates 3 phases of price action around a key EURUSD resistance level:
  Phase 1 – Sharp rejection (price bounces hard off level)
  Phase 2 – Multiple attacks weakening the level
  Phase 3 – Absorption (price stalls, ticks pile up, no bounce)
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timedelta
from axonai.realtime.level_tracker import LevelBehaviorTracker


class MockLevel:
    """Simplified PriceLevel stand-in."""
    def __init__(self, price, level_type="RESISTANCE", strength=0.7, direction="resistance"):
        self.price = price
        self.level_type = level_type
        self.strength = strength
        self.direction = direction


def make_tick(mid, volume=1000):
    bid = round(mid - 0.0001, 5)
    ask = round(mid + 0.0001, 5)
    return bid, ask


def simulate():
    # Use long approach duration so the absorption phase doesn't time out
    tracker = LevelBehaviorTracker(pip_mult=0.0001, max_approach_duration_sec=600)
    level = MockLevel(1.10500, "PDH", 0.7, "resistance")  # resistance at 1.10500
    base_time = datetime(2025, 5, 30, 8, 0, 0)

    print("=" * 72)
    print("  LEVEL BEHAVIOR TRACKER — LIVE SIMULATION")
    print("  Simulating EURUSD tick data around resistance at 1.10500")
    print("=" * 72)

    # ──────────────────────────────────────────────────────────
    # PHASE 1: Sharp Rejection  (price runs up, gets rejected)
    # ──────────────────────────────────────────────────────────
    print("\n" + "─" * 72)
    print("  PHASE 1 — Sharp Rejection")
    print("  Price approaches 1.10500 from below, then bounces hard ↓")
    print("─" * 72)

    levels = [level]
    tick_no = 0

    # Approach: price climbs toward resistance
    for i in range(10):
        p = 1.10400 + i * 0.00010  # 1.10400 → 1.10500
        bid, ask = make_tick(p)
        tracker.update(p, bid, ask, base_time + timedelta(seconds=i*3), 1500, levels)
        tick_no += 1

    print(f"   ... {tick_no} ticks approaching level")

    # Rejection: price gets rejected hard (bounces 8 pips in 2 seconds)
    bid, ask = make_tick(1.10480)
    tracker.update(1.10480, bid, ask, base_time + timedelta(seconds=30), 2500, levels)
    tick_no += 1

    bid, ask = make_tick(1.10400)
    tracker.update(1.10400, bid, ask, base_time + timedelta(seconds=32), 3000, levels)
    tick_no += 1

    bid, ask = make_tick(1.10350)
    tracker.update(1.10350, bid, ask, base_time + timedelta(seconds=34), 2800, levels)
    tick_no += 1

    print(f"   ... {tick_no} ticks total (rejection simulated)")

    summary = tracker.get_behavior_summary()
    for k, v in summary.items():
        print(f"\n  📊 Level {k}:")
        print(f"      Status:           {v['status']}")
        print(f"      Attacks:          {v['total_attacks']}")
        print(f"      Rejection count:  {v['rejection_count']}")
        print(f"      Last rej. vel:    {v['last_rejection_velocity']:.2f} pips/sec")
        print(f"      Quality:          {v['attack_quality']}")

    assert summary["1.10500"]["rejection_count"] == 1, "Expected 1 rejection"
    assert summary["1.10500"]["last_rejection_velocity"] > 0, "Expected velocity > 0"
    print("\n  ✅ Phase 1 PASSED: Sharp rejection detected")

    # ──────────────────────────────────────────────────────────
    # PHASE 2: Multiple Attacks (weakening the level)
    # ──────────────────────────────────────────────────────────
    print("\n" + "─" * 72)
    print("  PHASE 2 — Multiple Attacks (weakening)")
    print("  Price attacks the level 3 more times, each bounce gets smaller")
    print("─" * 72)

    for attack_round in range(3):
        t0 = base_time + timedelta(seconds=60 + attack_round * 45)

        # Approach again
        for i in range(5):
            p = 1.10400 + i * 0.00020
            bid, ask = make_tick(p)
            tracker.update(p, bid, ask, t0 + timedelta(seconds=i*2), 1200, levels)
            tick_no += 1

        # Decreasing bounce: first clears rejection threshold, last barely clears outer zone
        bounce_from_level = [8, 6, 5.1][attack_round]
        bounce_price = round(1.10500 - bounce_from_level * 0.0001, 5)
        bid, ask = make_tick(bounce_price)
        tracker.update(
            bounce_price,
            bid, ask,
            t0 + timedelta(seconds=12), 2000, levels,
        )
        tick_no += 1

        print(f"   Attack {attack_round + 2}: bounced {bounce_from_level} pips from level")

    summary = tracker.get_behavior_summary()
    v = summary["1.10500"]
    print(f"\n  📊 Level 1.10500 after Phase 2:")
    print(f"      Attacks:          {v['total_attacks']}")
    print(f"      Consecutive:      {v['consecutive_attacks']}")
    print(f"      Rejection count:  {v['rejection_count']}")
    print(f"      Quality:          {v['attack_quality']}")
    print(f"      Has pullback:     {v['has_pullback']}")

    assert v["total_attacks"] == 4, f"Phase 2: Expected 4 total attacks, got {v['total_attacks']}"
    assert v["rejection_count"] == 2, f"Phase 2: Expected 2 rejections, got {v['rejection_count']}"
    print("  ✅ Phase 2 PASSED: Multiple attacks tracked")

    # ──────────────────────────────────────────────────────────
    # PHASE 3: Absorption  (price stalls, ticks pile up)
    # ──────────────────────────────────────────────────────────
    print("\n" + "─" * 72)
    print("  PHASE 3 — Absorption Pattern")
    print("  Price sits at the level, accumulating ticks without progress ↓")
    print("─" * 72)

    # Attack the level again
    t0 = base_time + timedelta(seconds=300)
    # Price approaches from just inside outer zone (4.5 pips away)
    zone_entry = 1.10455  # ~4.5 pips from level
    bid, ask = make_tick(zone_entry)
    tracker.update(zone_entry, bid, ask, t0, 1000, levels)
    tick_no += 1

    # Move to the level
    for i in range(3):
        p = 1.10470 + i * 0.00010
        bid, ask = make_tick(p)
        tracker.update(p, bid, ask, t0 + timedelta(seconds=i*2), 1000, levels)
        tick_no += 1

    # Now stall at the level (absorption): 250 ticks at nearly the same price
    for i in range(250):
        p = 1.10495 + (i % 5) * 0.00001  # tiny oscillation (0.1 pip)
        bid, ask = make_tick(p)
        tracker.update(p, bid, ask, t0 + timedelta(seconds=10 + i * 0.5), 800, levels)
        tick_no += 1

    print(f"   ... {tick_no} total ticks processed")

    summary = tracker.get_behavior_summary()
    v = summary["1.10500"]

    print(f"\n  📊 Level 1.10500 after Phase 3 (absorption):")
    print(f"      Attacks:          {v['total_attacks']}")
    print(f"      Consecutive:      {v['consecutive_attacks']}")
    print(f"      Ticks in zone:    {v['ticks_in_zone']}")
    print(f"      Absorption ratio: {v['absorption_ratio']}")
    print(f"      Is absorbing:     {v['is_absorbing']}")
    print(f"      Imbalance:        {v['imbalance']}")
    print(f"      Quality:          {v['attack_quality']}")
    print(f"      Status:           {v['status']}")

    assert v["is_absorbing"] == True, f"Expected absorbing=True, got {v['is_absorbing']}"
    print("  ✅ Phase 3 PASSED: Absorption detected")

    # ──────────────────────────────────────────────────────────
    # FINAL SUMMARY
    # ──────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("  SIMULATION RESULT: ALL PHASES PASSED ✅")
    print("=" * 72)
    print(f"\n  Total ticks processed: {tick_no}")
    print(f"  Total levels tracked: {len(summary)}")
    print(f"\n  Level behavior snapshot:")
    for k, v in summary.items():
        print(f"    {k} | {v['type']} | quality={v['attack_quality']} | "
              f"absorbing={v['is_absorbing']} | rejections={v['rejection_count']} | "
              f"attacks={v['total_attacks']}")
    print()

    # Additional API tests
    print("  ── Integration API checks ──")
    attack_count = tracker.get_attack_count(1.10500)
    assert attack_count >= 4, f"get_attack_count failed: {attack_count}"
    print(f"  ✅ get_attack_count(1.10500) = {attack_count}")
    assert tracker.get_consecutive_attacks(1.10500) > 0, "get_consecutive_attacks failed"
    print(f"  ✅ get_consecutive_attacks(1.10500) = {tracker.get_consecutive_attacks(1.10500)}")
    assert tracker.is_absorbing(1.10500) == True, "is_absorbing failed"
    print(f"  ✅ is_absorbing(1.10500) = {tracker.is_absorbing(1.10500)}")

    bhv = tracker.get_level_behavior(1.10500)
    assert bhv is not None, "get_level_behavior failed"
    print(f"  ✅ get_level_behavior(1.10500) returned LevelBehavior")

    # Pruning test
    tracker.prune_old_behaviors(set(), max_age_seconds=0)
    assert tracker.get_level_behavior(1.10500) is None, "prune should have removed level"
    print(f"  ✅ prune_old_behaviors() removed stale level")

    # Reset test
    tracker.update(1.10400, 1.10390, 1.10410, datetime.now(), 1000, levels)
    tracker.reset()
    assert tracker.get_behavior_summary() == {}, "reset should clear everything"
    print(f"  ✅ reset() cleared all state")

    print("\n  ✅ ALL INTEGRATION API CHECKS PASSED")
    print()


if __name__ == "__main__":
    simulate()
