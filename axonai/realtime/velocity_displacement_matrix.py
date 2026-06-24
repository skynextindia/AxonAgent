"""Velocity-Displacement Matrix Lookup.

Pure function that maps velocity percentile + displacement class + location
to an action (HOLD, WATCH, PROTECT, EXIT). No state, no side effects.

Used by ExitEngine to determine urgency level and action type.
"""

from enum import Enum


class MatrixAction(Enum):
    """Actions from velocity-displacement matrix lookup."""
    HOLD = "HOLD"          # Price moving right, stay in trade
    WATCH = "WATCH"        # Price uncertain, monitor closely
    PROTECT = "PROTECT"    # Reduce risk, tighten stop
    EXIT = "EXIT"          # Close the trade


def lookup(
    velocity_percentile: float,
    displacement_class: str,
    at_structure: bool,
) -> MatrixAction:
    """
    Matrix lookup: velocity × displacement × location → action.

    Args:
        velocity_percentile: 0-100 (from VelocityNormalizer)
        displacement_class: "IMPULSE" | "TRAP" | "ABSORPTION" | "EXHAUSTION" | "NEUTRAL"
        at_structure: True if within config["at_structure_atr_threshold"] of a level

    Returns:
        MatrixAction (HOLD | WATCH | PROTECT | EXIT)

    Matrix Logic:

                           IMPULSE         TRAP          ABSORPTION     EXHAUSTION    NEUTRAL
    velocity > 80%         HOLD (good)    WATCH (fight)  WATCH (no mv)  EXIT (bad)    WATCH
    velocity 50-80%        HOLD           HOLD           PROTECT        PROTECT       PROTECT
    velocity 20-50%        HOLD           HOLD           HOLD           WATCH         HOLD
    velocity < 20%         WATCH          PROTECT        PROTECT        PROTECT       HOLD

    at_structure modifier (more conservative):
      - HOLD + at_structure → PROTECT (being at level is risky)
      - WATCH + at_structure → PROTECT (heightened caution)
      - PROTECT → PROTECT (no change, already cautious)
      - EXIT + at_structure → WATCH (retest trap gate: don't exit at level)
    """
    # Clamp velocity to 0-100
    vel = max(0.0, min(100.0, velocity_percentile))

    # Base matrix lookup
    if vel > 80:
        if displacement_class == "IMPULSE":
            base_action = MatrixAction.HOLD
        elif displacement_class == "TRAP":
            base_action = MatrixAction.WATCH
        elif displacement_class == "ABSORPTION":
            base_action = MatrixAction.WATCH
        elif displacement_class == "EXHAUSTION":
            base_action = MatrixAction.EXIT
        else:  # NEUTRAL or other
            base_action = MatrixAction.WATCH

    elif vel >= 50:
        if displacement_class == "IMPULSE":
            base_action = MatrixAction.HOLD
        elif displacement_class == "TRAP":
            base_action = MatrixAction.HOLD
        elif displacement_class == "ABSORPTION":
            base_action = MatrixAction.PROTECT
        elif displacement_class == "EXHAUSTION":
            base_action = MatrixAction.PROTECT
        else:
            base_action = MatrixAction.PROTECT

    elif vel >= 20:
        if displacement_class == "IMPULSE":
            base_action = MatrixAction.HOLD
        elif displacement_class == "TRAP":
            base_action = MatrixAction.HOLD
        elif displacement_class == "ABSORPTION":
            base_action = MatrixAction.HOLD
        elif displacement_class == "EXHAUSTION":
            base_action = MatrixAction.WATCH
        else:
            base_action = MatrixAction.HOLD

    else:  # vel < 20
        if displacement_class == "IMPULSE":
            base_action = MatrixAction.WATCH
        elif displacement_class == "TRAP":
            base_action = MatrixAction.PROTECT
        elif displacement_class == "ABSORPTION":
            base_action = MatrixAction.PROTECT
        elif displacement_class == "EXHAUSTION":
            base_action = MatrixAction.PROTECT
        else:
            base_action = MatrixAction.HOLD

    # Apply at_structure modifier (more conservative when at a level)
    if at_structure:
        if base_action == MatrixAction.HOLD:
            return MatrixAction.PROTECT
        elif base_action == MatrixAction.WATCH:
            return MatrixAction.PROTECT
        elif base_action == MatrixAction.EXIT:
            # RETEST TRAP GATE: Don't exit at structure, wait for displacement flip
            return MatrixAction.WATCH
        # PROTECT stays PROTECT

    return base_action


if __name__ == "__main__":
    # Smoke test: verify all matrix combinations
    test_cases = [
        # (velocity, displacement, at_structure, expected_action)
        (85, "IMPULSE", False, MatrixAction.HOLD),           # Good momentum
        (85, "ABSORPTION", False, MatrixAction.WATCH),       # High velocity, no move
        (85, "EXHAUSTION", False, MatrixAction.EXIT),        # Exhaustion at high vel
        (85, "EXHAUSTION", True, MatrixAction.WATCH),        # Retest trap gate (not EXIT)
        (75, "TRAP", False, MatrixAction.HOLD),              # Mid-high, trapped
        (75, "ABSORPTION", True, MatrixAction.PROTECT),      # at_structure modifier
        (45, "IMPULSE", False, MatrixAction.HOLD),           # Mid-range, good displacement
        (15, "EXHAUSTION", False, MatrixAction.PROTECT),     # Low velocity, exhaustion
        (15, "TRAP", True, MatrixAction.PROTECT),            # Low vel, at level
        (10, "NEUTRAL", False, MatrixAction.HOLD),           # Very low velocity
    ]

    passed = 0
    for vel, disp, at_struct, expected in test_cases:
        result = lookup(vel, disp, at_struct)
        if result == expected:
            passed += 1
            print(f"  [OK] ({vel:3.0f}, {disp:12s}, at_struct={at_struct}) -> {result.value}")
        else:
            print(f"  [FAIL] ({vel}, {disp}, {at_struct}) returned {result.value}, expected {expected.value}")

    print(f"\nvelocity_displacement_matrix.py: {passed}/{len(test_cases)} tests passed")
    if passed == len(test_cases):
        print("[PASS] All tests PASSED")
    else:
        print(f"[FAIL] {len(test_cases) - passed} tests FAILED")
        exit(1)
