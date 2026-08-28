"""Correlated-exposure model for the Risk Engine prototype.

PROTOTYPE. Conservative and explicit: EURUSD + USDJPY form ONE 'USD exposure
bucket'. We do NOT trust a correlation coefficient as a risk proxy — we AGGREGATE
signed USD exposure and same-direction stop-risk.

Do not expand beyond EURUSD + USDJPY without evidence (XAUUSD is stubbed but not
active). This mirrors production's netting formula but is not imported by it.

Source mirrored (re-implemented locally, NOT imported):
    axonai/realtime/correlation_engine.py:41-55  position_usd()
"""

from __future__ import annotations

from typing import List, Iterable
from dataclasses import dataclass

from .models import OpenPosition

_CONTRACT = 100_000.0

# The prototype USD-exposure group. Extend ONLY with evidence.
USD_BUCKET = ("EURUSD", "USDJPY")


def _canon(symbol: str) -> str:
    letters = "".join(c for c in (symbol or "").upper() if c.isalpha())
    return letters[:6] if len(letters) >= 6 else letters


def signed_usd_notional(symbol: str, direction: str, lot: float, price: float) -> float:
    """Signed USD notional (long-USD positive). Mirror of production position_usd.

    XXXUSD (quote USD): long pair = short USD -> -lot*contract*price
    USDXXX (base USD):  long pair = long  USD -> +lot*contract
    non-USD crosses: 0 (ignored for USD netting).
    """
    c = _canon(symbol)
    base, quote = c[:3], c[3:6]
    side = 1.0 if str(direction).upper().startswith("B") else -1.0
    if quote == "USD":
        return -side * lot * _CONTRACT * (price or 1.0)
    if base == "USD":
        return side * lot * _CONTRACT
    return 0.0


def usd_sign(symbol: str, direction: str) -> int:
    """+1 if the position is net LONG USD, -1 if net SHORT USD, 0 if no USD leg.

    NB: EURUSD BUY and USDJPY SELL are BOTH short-USD -> same sign (-1). That is
    the correlated bet that caused the 08-11 double flatten.
    """
    n = signed_usd_notional(symbol, direction, 1.0, 1.0)
    return (n > 0) - (n < 0)


@dataclass
class BucketExposure:
    """Aggregated USD-bucket exposure for a set of positions + a candidate."""
    net_notional: float          # signed sum of USD notional across the bucket
    gross_notional: float        # sum of |notional| (total USD exposure on the book)
    same_dir_risk_usd: float     # stop-risk $ of positions sharing candidate's USD sign
    candidate_sign: int
    n_bucket_positions: int


def aggregate_usd_bucket(
    positions: Iterable[OpenPosition],
    candidate_symbol: str,
    candidate_direction: str,
    candidate_lot: float,
    candidate_price: float,
    candidate_risk_usd: float,
) -> BucketExposure:
    """Aggregate USD exposure for the bucket, INCLUDING the prospective candidate.

    ``same_dir_risk_usd`` sums the stop-risk of already-open bucket positions that
    share the candidate's USD direction plus the candidate's own risk — this is
    the number a correlation cap should throttle (correlated tail).
    """
    cand_sign = usd_sign(candidate_symbol, candidate_direction)
    net = signed_usd_notional(candidate_symbol, candidate_direction, candidate_lot, candidate_price)
    gross = abs(net)
    same_dir = float(candidate_risk_usd or 0.0)
    n = 0
    for p in positions:
        if _canon(p.symbol) not in USD_BUCKET:
            continue
        n += 1
        note = signed_usd_notional(p.symbol, p.direction, p.lot, p.entry_price)
        net += note
        gross += abs(note)
        if cand_sign != 0 and usd_sign(p.symbol, p.direction) == cand_sign:
            same_dir += float(p.open_risk_usd or 0.0)
    return BucketExposure(
        net_notional=net,
        gross_notional=gross,
        same_dir_risk_usd=same_dir,
        candidate_sign=cand_sign,
        n_bucket_positions=n,
    )
