"""PURE SHADOW direction challenger. Never reaches the executor.

Contract:
  * Input  = only the features available at the live decision point (a Sample).
  * Output = BUY / SELL / HOLD + reason codes.
  * It does NOT invent a direction. It consumes the production fade direction and
    may only VETO it (-> HOLD) when a CONTINUATION signature is detected. Keeping
    the trade returns the SAME direction the daemon produced. (A veto-only filter,
    like every existing live gate — it can only remove trades, never add or flip.)
  * Pure function of (Sample, ChallengerPolicy). No I/O, no live handles, no
    imports from axonai / MT5.

The continuation signature is the hypothesis under test, expressed over the
features the telemetry actually has. Thresholds live in ChallengerPolicy and are
fit ONLY on the walk-forward TRAIN slice — never on the full data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .dataset import Sample


@dataclass(frozen=True)
class ChallengerPolicy:
    """Continuation-veto thresholds. All optional; a None disables that sub-rule.

    A sub-rule fires when its feature is PRESENT and past threshold. Missing
    features never fire a veto (we do not veto on absent data). The trade is
    vetoed to HOLD when the number of fired sub-rules >= ``min_votes``.
    """
    name: str = "unnamed"
    # continuation = strong displacement (faded into a real impulse)
    displacement_min: Optional[float] = None
    # continuation = persistently efficient one-way move (high $/tick efficiency)
    efficiency_min: Optional[float] = None
    # continuation = weak exhaustion signal (low velocity_divergence)
    velocity_div_max: Optional[float] = None
    # continuation = faded against a non-neutral MTF bias
    veto_against_mtf: bool = False
    # votes needed to veto
    min_votes: int = 1

    def describe(self) -> str:
        bits = []
        if self.displacement_min is not None:
            bits.append(f"disp>={self.displacement_min}")
        if self.efficiency_min is not None:
            bits.append(f"eff>={self.efficiency_min}")
        if self.velocity_div_max is not None:
            bits.append(f"veldiv<={self.velocity_div_max}")
        if self.veto_against_mtf:
            bits.append("against_mtf")
        return f"{self.name}[{','.join(bits) or 'noop'}; votes>={self.min_votes}]"


def _against_mtf(direction: Optional[str], mtf: Optional[str]) -> bool:
    if not direction or not mtf:
        return False
    m = mtf.upper()
    return (direction == "SELL" and m == "BULLISH") or (direction == "BUY" and m == "BEARISH")


def decide(s: Sample, policy: ChallengerPolicy) -> Tuple[str, List[str], int]:
    """Return (action, reason_codes, votes). action in {BUY, SELL, HOLD}.

    KEEP => the production direction is returned unchanged. VETO => HOLD.
    """
    keep = s.direction if s.direction in ("BUY", "SELL") else "HOLD"
    if keep == "HOLD":
        return "HOLD", ["no_production_direction"], 0

    reasons: List[str] = []
    votes = 0

    if policy.displacement_min is not None and s.displacement_ratio is not None:
        if s.displacement_ratio >= policy.displacement_min:
            votes += 1
            reasons.append(f"strong_displacement({s.displacement_ratio:.2f})")

    if policy.efficiency_min is not None and s.tick_efficiency is not None:
        if s.tick_efficiency >= policy.efficiency_min:
            votes += 1
            reasons.append(f"persistent_efficiency({s.tick_efficiency:.4f})")

    if policy.velocity_div_max is not None and s.velocity_divergence is not None:
        if s.velocity_divergence <= policy.velocity_div_max:
            votes += 1
            reasons.append(f"weak_exhaustion(veldiv={s.velocity_divergence:.3f})")

    if policy.veto_against_mtf and _against_mtf(s.direction, s.mtf_alignment):
        votes += 1
        reasons.append(f"against_mtf({s.mtf_alignment})")

    if votes >= policy.min_votes and votes > 0:
        return "HOLD", reasons or ["continuation_signature"], votes
    return keep, ["keep_fade"], votes


def decide_all(samples: List[Sample], policy: ChallengerPolicy):
    return [decide(s, policy) for s in samples]
