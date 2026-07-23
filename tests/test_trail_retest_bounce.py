"""Retest detection must require a confirmed bounce, not mere proximity to the SL.

The old _detect_retest returned True whenever current price sat within the window
of the SL, ignoring both `lowest_price` (the adverse extreme the daemon tracks)
and `retest_bounce_pips` (defined but never read). Because a retest arms the
trail, that meant price falling toward the stop tightened the stop further the
closer it got. These tests pin the corrected behaviour: a test of the SL zone
followed by a bounce off it.
"""

from axonai.realtime.velocity_trailing import VelocityTrailingManager

PIP = 0.0001


def _mgr(**cfg):
    m = VelocityTrailingManager(config=cfg)
    m._trail_state[1] = {"retest_count": 0}
    return m


def test_buy_falling_toward_sl_is_not_a_retest():
    """Price in the zone but still at its low (no bounce) must not count."""
    m = _mgr()
    # SL at 1.1000, price down at 1.10015 which is its own low: touched, no bounce.
    got = m._detect_retest(
        ticket=1, position_type="BUY", bid=1.10015, ask=1.10016,
        current_sl=1.1000, lowest_price=1.10015, pip=PIP, retest_window_pips=3.0)
    assert got is False


def test_buy_bounce_off_sl_zone_is_a_retest():
    """Low tested the zone (1.5p from SL) and price bounced 1.5p back up."""
    m = _mgr()
    got = m._detect_retest(
        ticket=1, position_type="BUY", bid=1.10030, ask=1.10031,
        current_sl=1.1000, lowest_price=1.10015, pip=PIP, retest_window_pips=3.0)
    assert got is True


def test_buy_bounce_without_touch_is_not_a_retest():
    """Never got near the SL zone -> not a retest even with a bounce."""
    m = _mgr()
    # Low was 1.1010 = 10p above a 1.1000 SL, outside a 3p window.
    got = m._detect_retest(
        ticket=1, position_type="BUY", bid=1.10130, ask=1.10131,
        current_sl=1.1000, lowest_price=1.1010, pip=PIP, retest_window_pips=3.0)
    assert got is False


def test_sell_bounce_off_sl_zone_is_a_retest():
    """SELL: SL above, adverse extreme is the max ask; price came back down."""
    m = _mgr()
    # SL 1.1000, high ask 1.10985 (1.5p from SL), price back down to 1.10970.
    got = m._detect_retest(
        ticket=1, position_type="SELL", bid=1.10969, ask=1.10970,
        current_sl=1.1100, lowest_price=1.10985, pip=PIP, retest_window_pips=3.0)
    assert got is True


def test_legacy_proximity_mode_restorable():
    """trail_retest_require_bounce=False restores proximity-only detection."""
    m = _mgr(trail_retest_require_bounce=False)
    got = m._detect_retest(
        ticket=1, position_type="BUY", bid=1.10015, ask=1.10016,
        current_sl=1.1000, lowest_price=1.10015, pip=PIP, retest_window_pips=3.0)
    assert got is True  # near the SL now; legacy fires on proximity alone
