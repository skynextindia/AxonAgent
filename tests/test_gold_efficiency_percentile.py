"""Self-calibrating tick-efficiency percentile (gold over-entry fix).

Gold's raw tick_efficiency sits structurally low (~0.09 vs FX ~0.33) because
gold streams ~700x more ticks per unit time, inflating the total-path
denominator. Absolute cutoffs like `eff < 0.2` therefore fire on EVERY gold
tick, handing the confluence gate a free +0.3 climax credit each tick and
over-entering gold.

The fix: a self-calibrating `tick_efficiency_percentile` that ranks each tick's
efficiency against that symbol's OWN rolling distribution, mirroring the
existing velocity percentile machinery. The reversal model's climax credit
becomes config-gated:
  - FX  : `entry_climax_eff_percentile` unset -> absolute `eff < 0.2` (unchanged)
  - Gold: `entry_climax_eff_percentile = 28`  -> percentile cutoff (self-cal)
"""

from __future__ import annotations

import types
from datetime import datetime, timedelta

import pytest

from axonai.realtime.velocity_normalizer import VelocityNormalizer, NormalizedVelocity
from axonai.realtime.reversal_model import _unified_confluence_score


PIP = 0.0001


def _feed_varied(norm: VelocityNormalizer, n_blocks: int = 30):
    """Feed alternating impulse/chop blocks so efficiency spans a wide range.

    Impulse block -> monotonic run -> efficiency near 1.0.
    Chop block    -> oscillating run -> efficiency near 0.0.
    1s tick spacing keeps ~10-11 ticks inside the 10s efficiency window.
    """
    t0 = datetime(2026, 1, 5, 10, 0, 0)
    sec = 0
    price = 1.10000
    outs: list[NormalizedVelocity] = []
    for block in range(n_blocks):
        impulse = (block % 2 == 0)
        for i in range(12):
            if impulse:
                price += PIP
            else:
                price += PIP if (i % 2 == 0) else -PIP
            sec += 1
            outs.append(norm.update(price, t0 + timedelta(seconds=sec)))
    return outs


# ── Machinery: self-calibrating efficiency percentile ─────────────────────────

def test_efficiency_percentile_warmup_guard_returns_50():
    """Fewer than 10 accumulated samples -> 50.0 (mirrors `_percentile`)."""
    fresh = VelocityNormalizer(pip_mult=PIP)
    assert fresh._efficiency_percentile(0.5) == 50.0


def test_efficiency_percentile_ranks_within_own_distribution():
    """Low efficiency ranks below high efficiency in the symbol's own window."""
    norm = VelocityNormalizer(pip_mult=PIP)
    _feed_varied(norm)

    vals = norm._sorted_efficiencies
    assert len(vals) >= 10

    lo = norm._efficiency_percentile(vals[0])
    hi = norm._efficiency_percentile(vals[-1])

    assert 0.0 <= lo <= 100.0
    assert 0.0 <= hi <= 100.0
    assert lo < hi


def test_tick_efficiency_percentile_field_self_calibrates():
    """update() emits a `tick_efficiency_percentile` that spans a wide range."""
    norm = VelocityNormalizer(pip_mult=PIP)
    outs = _feed_varied(norm)

    warm = [o.tick_efficiency_percentile for o in outs[60:]]
    assert warm, "expected warmed-up samples"
    assert all(0.0 <= p <= 100.0 for p in warm)
    # A genuinely calibrating metric visits both extremes, not a stuck constant.
    assert (max(warm) - min(warm)) > 40.0


# ── Integration: reversal-model climax credit gate ────────────────────────────

def _mtf(warm: bool = False):
    b = 0.5 if warm else 0.0
    return types.SimpleNamespace(
        h4_bias=b, h1_bias=b, m15_bias=b,
        is_exhaustion_zone=False, is_pullback=False, reversal_pressure=0.0,
    )


def _liq():
    return types.SimpleNamespace(
        active_breaks=[], active_sweeps=[], liquidity_void_active=False,
    )


def _disp():
    return types.SimpleNamespace(is_exhausting=False, classification="")


def _vel(tick_efficiency: float, tick_efficiency_percentile: float):
    return types.SimpleNamespace(
        percentile=50.0, decay_ratio=1.0, is_decaying=False, is_unusual=False,
        tick_efficiency=tick_efficiency,
        tick_efficiency_percentile=tick_efficiency_percentile,
    )


def _score(vel, config):
    """Confluence score with MTF cold + candle setup 0.4 + no S/R levels.

    Reduces to 0.30*0.4 + 0.25*vel_score + 0.10 (cold-MTF credit); the only
    moving part is the climax +0.3 in vel_score, so score deltas isolate it.
    """
    ok, score, reason = _unified_confluence_score(
        direction="SELL", price=1.10000, pip=PIP, h1_atr=0.0,
        mtf=_mtf(), liq=_liq(), vel=vel, disp=_disp(),
        price_levels=None, candle_setup_score=0.4, config=config,
    )
    return score


CLIMAX_CREDIT = 0.25 * 0.3  # 0.075


def test_fx_ignores_percentile_uses_absolute_threshold():
    """No `entry_climax_eff_percentile` -> absolute `eff < 0.2`, field ignored."""
    cfg = {}
    # eff below 0.2 earns the climax credit regardless of a high percentile.
    hi_pct_low_eff = _score(_vel(tick_efficiency=0.15, tick_efficiency_percentile=95.0), cfg)
    # eff at/above 0.2 does not, regardless of a low percentile.
    lo_pct_high_eff = _score(_vel(tick_efficiency=0.30, tick_efficiency_percentile=5.0), cfg)

    assert hi_pct_low_eff - lo_pct_high_eff == pytest.approx(CLIMAX_CREDIT, abs=1e-9)


def test_gold_uses_percentile_not_absolute():
    """`entry_climax_eff_percentile=28` -> percentile decides, absolute ignored."""
    cfg = {"entry_climax_eff_percentile": 28, "symbol": "XAUUSD"}
    # Structurally-low gold eff (0.05) that is NOT unusual for gold (pct 90):
    # under the old absolute rule this over-credited every tick; now it must not.
    typical_gold = _score(_vel(tick_efficiency=0.05, tick_efficiency_percentile=90.0), cfg)
    # Genuinely low-for-gold efficiency (bottom decile) earns the credit.
    exhausted_gold = _score(_vel(tick_efficiency=0.05, tick_efficiency_percentile=10.0), cfg)

    assert exhausted_gold - typical_gold == pytest.approx(CLIMAX_CREDIT, abs=1e-9)
