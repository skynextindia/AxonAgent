#!/usr/bin/env python3
"""
Demo: Velocity-Based Trailing Stop Management

Shows how trailing SL gets modified in real-time based on:
  • Velocity percentile (market activity)
  • Displacement ratio (trending strength)
  • Health score (trade thesis confidence)
  • at_structure flag (location relative to S/R)

Simulates 50 ticks of price movement with varying velocity/displacement.
"""

from axonai.realtime.velocity_trailing import VelocityTrailingManager

def demo():
    print("\n" + "=" * 80)
    print("VELOCITY-BASED TRAILING STOP MANAGEMENT DEMO")
    print("=" * 80 + "\n")

    manager = VelocityTrailingManager()

    # BUY position setup
    ticket = 123456
    position_type = "BUY"
    entry_price = 1.13534
    initial_sl = 1.13334  # 20 pips below entry
    pip = 0.0001

    print(f"POSITION OPENED:")
    print(f"  Ticket: #{ticket}")
    print(f"  Type: {position_type} 0.01 lots")
    print(f"  Entry: {entry_price:.5f}")
    print(f"  Initial SL: {initial_sl:.5f} (20 pips risk)")
    print(f"  TP: None (managed by exit engine)\n")

    print("=" * 80)
    print("SIMULATING 50 TICKS WITH VARYING VELOCITY/DISPLACEMENT:")
    print("=" * 80 + "\n")

    current_sl = initial_sl
    initial_sl_distance = (entry_price - initial_sl) / pip

    # Simulate tick sequence with different market conditions
    scenarios = [
        # (tick, price, vel_percentile, displacement_ratio, health, at_structure)
        (1, 1.13544, 20, 0.1, 100, False),    # Low vel, low disp - conservative
        (2, 1.13554, 25, 0.15, 95, False),   # Still building
        (3, 1.13564, 50, 0.25, 90, False),   # Velocity picking up
        (4, 1.13574, 65, 0.35, 85, False),   # Medium vel, good disp - trail!
        (5, 1.13584, 75, 0.40, 80, False),   # HIGH vel, trending strong - aggressive trail
        (6, 1.13594, 85, 0.45, 75, False),   # Peak velocity
        (7, 1.13584, 80, 0.42, 70, False),   # Slight pullback
        (8, 1.13604, 75, 0.50, 72, False),   # Continuing up
        (9, 1.13614, 70, 0.55, 74, False),   # Still strong
        (10, 1.13624, 65, 0.60, 76, False),  # Momentum holding
        (11, 1.13614, 55, 0.50, 78, True),   # Pullback, now AT_STRUCTURE - be careful
        (12, 1.13604, 45, 0.45, 80, True),   # At structure, health recovering
        (13, 1.13594, 35, 0.40, 82, True),   # Low vel at structure - hold tight
        (14, 1.13614, 40, 0.48, 81, False),  # Bouncing off structure
        (15, 1.13634, 60, 0.58, 79, False),  # Velocity up again
    ]

    for tick, price, vel_perc, disp_ratio, health, at_struct in scenarios:
        bid = price
        ask = price + 0.0003  # 3 pips spread

        result = manager.on_tick(
            ticket=ticket,
            bid=bid,
            ask=ask,
            position_type=position_type,
            entry_price=entry_price,
            initial_sl=initial_sl,
            current_sl=current_sl,
            velocity_percentile=vel_perc,
            displacement_ratio=disp_ratio,
            health_score=health,
            at_structure=at_struct,
        )

        profit = (bid - entry_price) / pip

        # Print tick info
        print(f"TICK {tick:2d} | Price: {bid:.5f} | Profit: +{profit:6.1f}pips | " +
              f"Vel:{vel_perc:3.0f}% Disp:{disp_ratio:.2f} Health:{health:3.0f}% " +
              f"AtStr:{str(at_struct):<5}")

        # If SL was modified
        if result:
            print(f"        *** SL TRAILED ***")
            print(f"            Aggressiveness: {result['aggressiveness']:.2f}")
            print(f"            Reason: {result['reason']}")
            print(f"            Old SL: {current_sl:.5f}")
            print(f"            New SL: {result['new_sl']:.5f}")
            print(f"            Profit Locked: {result['profit_locked']:.1f} pips")
            current_sl = result["new_sl"]
        else:
            print(f"        SL: {current_sl:.5f} (no change)")

        print()

    print("=" * 80)
    print("SUMMARY:")
    print("=" * 80)
    print(f"\nInitial SL: {initial_sl:.5f}")
    print(f"Final SL:   {current_sl:.5f}")
    print(f"Profit at Final Price: +{(1.13634 - entry_price) / pip:.1f} pips")
    print(f"Protected Profit: +{(current_sl - entry_price) / pip:.1f} pips (even if price reverses now)\n")

    print("KEY INSIGHTS:")
    print("  [1] High velocity (75%+) + good displacement = AGGRESSIVE trail every 3 ticks")
    print("  [2] Medium velocity (40-70%) + normal displacement = NORMAL trail every 10 ticks")
    print("  [3] Low velocity (<40%) + unfavorable = CONSERVATIVE, hold tight")
    print("  [4] at_structure = TRUE lowers aggressiveness (don't trail as far at levels)")
    print("  [5] Health score drives trail distance (good health = trail more)")
    print("\nRESULT: SL continuously locks in profits as market velocity increases,")
    print("        but becomes more conservative when at support/resistance levels.\n")

if __name__ == "__main__":
    demo()
