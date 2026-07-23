"""Entry State Machine.

Replaces the legacy stateless boolean EntryGate. Implements a 5-state
machine to prevent entering too early on the raw microstructure peak,
forcing price to confirm direction via displacement first.

States:
0. IDLE        - Waiting for microstructure anomaly
1. ANOMALY     - Velocity/Volume spike detected (Peak)
2. ARMING      - Displacement failing (Trap/Absorption forming)
3. TRIGGERED   - Genuine structural break away from the trap
4. INVALIDATED - Anomaly disappeared, trap failed, or timeout

Only State 3 returns a valid "BUY"/"SELL" signal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from collections import deque
logger = logging.getLogger(__name__)

from axonai.realtime.velocity_normalizer import NormalizedVelocity
from axonai.realtime.displacement_engine import DisplacementState, DISPLACEMENT_IMPULSE, DISPLACEMENT_TRAP, DISPLACEMENT_ABSORPTION
from axonai.realtime.liquidity_engine import LiquidityState
from axonai.realtime.mtf_context import MTFState

if TYPE_CHECKING:
    from axonai.realtime.regime_engine import RegimeState
    from axonai.realtime.market_context import MarketContext


# ── Entry States ─────────────────────────────────────────────────────────
STATE_IDLE = "IDLE"
STATE_ANOMALY = "ANOMALY"
STATE_ARMING = "ARMING"
STATE_RETEST_WAIT = "RETEST_WAIT"
STATE_TRIGGERED = "TRIGGERED"
STATE_INVALIDATED = "INVALIDATED"


@dataclass
class EntryDecision:
    """The output of the entry state machine."""
    state: str = STATE_IDLE
    is_valid_entry: bool = False
    direction: Optional[str] = None              # "BUY" or "SELL"
    signal_quality: float = 0.0                  # 0.0 to 1.0
    reason: str = "Awaiting anomaly"
    skip_reason: str = ""                         # populated by reversal-confluence gate veto (observability)

    # New optional fields (populated by daemon.py dependency injection)
    entry_location_context: Optional[dict] = None      # from LocationEngine
    entry_regime: Optional[str] = None                 # from RegimeEngine
    entry_velocity_percentile: Optional[float] = None  # from VelocityNormalizer
    entry_displacement_class: Optional[str] = None     # from DisplacementEngine


def _num(obj, attr: str, default: float) -> float:
    """Read a numeric attribute, falling back only when it is missing or None.

    Replaces `getattr(obj, attr, default) or default`, which also swallows 0.0
    because it is falsy. For decay_ratio that inverted the signal at its most
    informative value: 0.0 is total velocity decay, the ideal retest, and became
    1.0, which trips the `decay > 0.8` high-momentum invalidation.
    """
    val = getattr(obj, attr, None)
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


class EntryStateMachine:
    """Stateful trade entry execution manager."""

    def __init__(self, pip_mult: float = 0.0001, timeout_sec: float = 120.0, config: Optional[dict] = None):
        # Late import to avoid circular dependency
        from axonai.realtime.regime_engine import REGIME_RANGE_CHOP, REGIME_EXHAUSTION, REGIME_REVERSAL

        self.REGIME_RANGE_CHOP = REGIME_RANGE_CHOP
        self.REGIME_EXHAUSTION = REGIME_EXHAUSTION
        self.REGIME_REVERSAL = REGIME_REVERSAL

        self._pip = pip_mult
        self._timeout_sec = timeout_sec
        self._config = config or {}
        # Sniper-entry tunables (pair-scalable; FX defaults preserve behavior).
        _scale = float(self._config.get("pair_move_scale", 1.0))
        self._climax_efficiency_max = float(self._config.get("entry_climax_efficiency_max", 0.2))
        self._mae_invalidate_pips = float(self._config.get("entry_mae_invalidate_pips", 5.0 * _scale))
        # When True, a plain-impulse breakaway routes through RETEST_WAIT for a
        # velocity-decay exhaustion confirmation (sniper), instead of triggering
        # on the breakout itself. A clear EXHAUSTION signature still triggers now.
        self._require_retest_confirm = bool(self._config.get("entry_require_retest_confirm", True))
        self._prev_net_sign = 0  # displacement-flip detector

        # State tracking
        self._current_state = STATE_IDLE
        self._anomaly_time: float = 0.0
        self._anomaly_price: float = 0.0
        self._anomaly_direction: str = ""   # Direction we expect the reversal
        self._anomaly_type: str = ""        # "sweep" or "climax"
        self._max_adverse_excursion: float = 0.0
        self._last_tick_time: float = 0.0
        
        # Diagnostic
        self._last_reason = "Initialized"

        # Spread history for smoothing
        self._spread_history: deque[float] = deque(maxlen=5)
        self._smoothed_spread_pips: float = 1.0
        self._retest_start_time: float = 0.0
        # Furthest reversal-side excursion (in pips, signed) reached during the current
        # retest. Used by the directional-approach gate so a SELL only triggers after
        # price pulled away from the top and rallied BACK toward it (a real retest),
        # not on the first momentum stall on the way down (which fired at the bottom).
        self._retest_extreme: float = 0.0
        self._retest_timeout_sec: float = float(self._config.get("entry_retest_timeout_sec", 300.0))
        # Running tally of how RETEST_WAIT resolves. RETEST_WAIT is now the only route
        # to an entry, so if it stops resolving the trade rate goes to zero silently.
        # Counted rather than inferred so the reason is visible in the log.
        self._retest_outcomes: dict[str, int] = {}
        # The most extreme PRICE actually observed since the anomaly (the high for a
        # SELL setup, the low for a BUY). Distinct from _anomaly_price, which is the
        # structural S/R level the setup formed at. The breakout fast-kills mean "price
        # made a NEW extreme", so they must measure against the observed extreme; once
        # _anomaly_price became a level rather than a tick, measuring against it turned
        # those checks into "price is on the wrong side of the level", which a normal
        # retest wick trips on its way back.
        self._anomaly_extreme_price: float = 0.0
        
        # Stall tracking
        self._arm_start_time: float = 0.0
        self._min_stall_duration: float = float(self._config.get("entry_min_stall_duration", 15.0))

        # TRIGGERED TTL: a trigger the executor/gate never consumed goes stale —
        # its anomaly price stops describing the market. Without this, a vetoed
        # trigger lingered indefinitely and could fire hours later, far from the
        # level that justified it (limit/retest entry styles).
        self._triggered_time: float = 0.0
        self._triggered_ttl_sec: float = float(self._config.get("entry_trigger_ttl_sec", 120.0))

    def reset(self) -> None:
        """Force the machine back to IDLE."""
        self._current_state = STATE_IDLE
        self._anomaly_time = 0.0
        self._anomaly_price = 0.0
        self._anomaly_direction = ""
        self._anomaly_type = ""
        self._max_adverse_excursion = 0.0
        self._anomaly_extreme_price = 0.0
        self._last_tick_time = 0.0
        self._retest_start_time = 0.0
        self._arm_start_time = 0.0
        self._prev_net_sign = 0
        self._triggered_time = 0.0
        self._last_reason = "Reset"

    def evaluate(
        self,
        price: float,
        timestamp: datetime,
        velocity: NormalizedVelocity,
        displacement: DisplacementState,
        liquidity: LiquidityState,
        regime: "RegimeState",
        mtf: MTFState,
        spread: float = 0.0,
        candle_setup_active: bool = False,
        candle_setup_direction: Optional[str] = None,
        candle_setup_level: float = 0.0,
    ) -> EntryDecision:
        """Evaluate conditions and transition states."""
        # Smooth spread over 3-5 ticks
        pip = self._pip
        tick_spread = spread / pip if spread > 0.0 else 1.0
        self._spread_history.append(tick_spread)
        self._smoothed_spread_pips = sum(self._spread_history) / len(self._spread_history)

        ts = timestamp.timestamp() if isinstance(timestamp, datetime) else float(timestamp)
        
        # Adjust anomaly time if there was a large gap between ticks (e.g. candle gap in backtest)
        if self._last_tick_time > 0.0 and self._anomaly_time > 0.0:
            gap = ts - self._last_tick_time
            if gap > 5.0:
                self._anomaly_time += gap
                logger.debug("EntryStateMachine: Adjusted anomaly_time by %.1f seconds due to tick gap", gap)
                
        self._last_tick_time = ts
        
        # 1. Timeout Check
        if self._current_state in (STATE_ANOMALY, STATE_ARMING):
            elapsed = ts - self._anomaly_time
            if elapsed > self._timeout_sec:
                self._transition(STATE_INVALIDATED, f"Timeout after {elapsed:.1f}s")
                
        # 2. State Machine Transitions
        if self._current_state in (STATE_IDLE, STATE_INVALIDATED):
            self._evaluate_idle(
                price, ts, velocity, displacement, liquidity, regime,
                candle_setup_active=candle_setup_active,
                candle_setup_direction=candle_setup_direction,
                candle_setup_level=candle_setup_level,
            )

        elif self._current_state == STATE_ANOMALY:
            self._evaluate_anomaly(price, ts, velocity, displacement)
            
        elif self._current_state == STATE_ARMING:
            self._evaluate_arming(price, ts, displacement, velocity)
            
        elif self._current_state == STATE_RETEST_WAIT:
            self._evaluate_retest_wait(price, ts, velocity)
            
        elif self._current_state == STATE_TRIGGERED:
            # Linger until consumed by the executor — but only within the TTL.
            if self._triggered_time == 0.0:
                self._triggered_time = ts
            elif ts - self._triggered_time > self._triggered_ttl_sec:
                self._transition(STATE_INVALIDATED,
                                 f"Trigger expired unconsumed after {ts - self._triggered_time:.0f}s")
                self._triggered_time = 0.0

        # 3. Decision generation
        is_trigger = self._current_state == STATE_TRIGGERED
        quality = self._calculate_quality(regime, mtf) if is_trigger else 0.0

        reason = self._last_reason
        if is_trigger:
            reason = f"Displacement away from trap confirmed ({self._anomaly_type})"

        # Include direction when actively tracking an anomaly (not in IDLE or INVALIDATED)
        tracking_anomaly = self._current_state in (STATE_ANOMALY, STATE_ARMING, STATE_TRIGGERED)
        return EntryDecision(
            state=self._current_state,
            is_valid_entry=is_trigger,
            direction=self._anomaly_direction if tracking_anomaly else None,
            signal_quality=round(quality, 2),
            reason=reason
        )

    def evaluate_with_context(
        self,
        price: float,
        timestamp: datetime,
        market_context: "MarketContext",
    ) -> EntryDecision:
        """
        Evaluate entry decision using MarketContext quality scores.

        Uses quality scores from MarketContext to make smarter decisions:
        - Skips entry if stop_hunt_detected (false reversal)
        - Waits for confirmation if displacement_phase is EARLY
        - Skips if reversal_confidence is too low (ambiguous)
        - Allows entry when displacement_phase is CONFIRMED
        - Scales signal_quality based on signal_agreement_score

        Args:
            price: Current market price
            timestamp: Tick time
            market_context: MarketContext with quality scores

        Returns:
            EntryDecision with improved quality scoring
        """
        # Extract components from market_context for normal state machine evaluation
        decision = self.evaluate(
            price=price,
            timestamp=timestamp,
            velocity=market_context.velocity,
            displacement=market_context.displacement,
            liquidity=market_context.liquidity,
            regime=market_context.regime,
            mtf=market_context.mtf,
        )

        # Now enhance decision based on MarketContext quality scores
        # Layer 1: Stop Hunting Detection (high priority filter)
        if market_context.stop_hunt_detected:
            if market_context.stop_hunt_phase == "HUNTING":
                # Active stop hunting, skip entry (false reversal)
                return EntryDecision(
                    state=decision.state,
                    is_valid_entry=False,
                    direction=decision.direction,
                    signal_quality=0.0,
                    reason=f"Stop hunting detected ({market_context.stop_hunt_phase}). Skipping entry.",
                )
            elif market_context.stop_hunt_phase == "SWEEPING":
                # Stops being swept, wait for reversal confirmation
                return EntryDecision(
                    state=decision.state,
                    is_valid_entry=False,
                    direction=decision.direction,
                    signal_quality=decision.signal_quality * 0.5,
                    reason="Stops being swept. Awaiting reversal confirmation.",
                )
            # If stop_hunt_phase == "REVERSING", allow normal evaluation to proceed

        # Layer 2: Reversal Lag Detection (timing optimization)
        if market_context.reversal_lag_ticks > 5:
            # Signal too delayed, opportunity likely passed
            return EntryDecision(
                state=decision.state,
                is_valid_entry=False,
                direction=decision.direction,
                signal_quality=0.0,
                reason=f"Signal lagged {market_context.reversal_lag_ticks} ticks. Opportunity expired.",
            )

        # Layer 3: Displacement Phase Check (confirmation gate)
        if self._current_state in (STATE_ANOMALY, STATE_ARMING):
            if market_context.displacement_phase == "EARLY":
                # Signal just forming, wait for confirmation
                return EntryDecision(
                    state=decision.state,
                    is_valid_entry=False,
                    direction=decision.direction,
                    signal_quality=decision.signal_quality * 0.6,
                    reason="Displacement in EARLY phase. Waiting for confirmation.",
                )
            elif market_context.displacement_phase == "CONFIRMED":
                # Signal confirmed, upgrade quality
                decision.signal_quality = min(1.0, decision.signal_quality * 1.2)

        # Layer 4: Reversal Confidence Threshold (ambiguity filter)
        if market_context.reversal_confidence < 50.0:
            # Ambiguous signal, skip entry
            return EntryDecision(
                state=decision.state,
                is_valid_entry=False,
                direction=decision.direction,
                signal_quality=0.0,
                reason=f"Reversal confidence too low ({market_context.reversal_confidence:.0f}%). Ambiguous.",
            )

        # Layer 5: Signal Agreement Scoring (consensus weighting)
        agreement_weight = market_context.signal_agreement_score / 100.0
        decision.signal_quality = decision.signal_quality * agreement_weight

        # Layer 6: Entry Window Expiration (opportunity closing)
        if market_context.entry_window_closing and market_context.ticks_until_confirmation_expires < 5:
            # Window closing fast, raise urgency
            if decision.is_valid_entry:
                decision.reason = f"{decision.reason} (URGENT: {market_context.ticks_until_confirmation_expires} ticks to expiration)"

        return decision

    def _transition(self, new_state: str, reason: str) -> None:
        old_state = self._current_state
        self._current_state = new_state
        self._last_reason = reason
        logger.info("EntryStateMachine: Transition %s -> %s | Reason: %s", old_state, new_state, reason)

    def _evaluate_idle(
        self, price: float, ts: float, vel: NormalizedVelocity,
        disp: DisplacementState, liq: LiquidityState, regime: RegimeState,
        candle_setup_active: bool = False,
        candle_setup_direction: Optional[str] = None,
        candle_setup_level: float = 0.0,
    ) -> None:
        """Look for the initial anomaly (Microstructure Peak).

        With the candle setup gate enabled (default), the machine only arms when
        a candle-close confirmed setup is active. This prevents entries on raw
        tick noise between M15 candle closes.
        """
        # ── Candle Setup Gate ─────────────────────────────────────────────────
        # If the gate is configured and no candle setup is active, stay IDLE.
        # This is the key integration point: candle_close → CandleSetupTracker
        # → setup_active before the tick-level machine can arm.
        gate_enabled = self._config.get("candle_setup_gate", True)
        if gate_enabled and not candle_setup_active:
            logger.debug(
                "EntryIDLE: Candle setup gate BLOCKED — no active M15 setup. Tick-level signals ignored."
            )
            return

        # ── Standard anomaly detection (unchanged) ───────────────────────────
        # Anomaly criteria: High velocity + low tick efficiency (Climax)
        is_climax = vel.is_unusual and vel.tick_efficiency < self._climax_efficiency_max

        # Or an active liquidity sweep
        is_sweep = len(liq.active_sweeps) > 0

        # Diagnostic: Log why no anomaly detected
        if not is_climax and not is_sweep:
            logger.debug("EntryIDLE: No anomaly. vel_unusual=%s eff=%.3f sweeps=%d", vel.is_unusual, vel.tick_efficiency, len(liq.active_sweeps))

        # ── Expected reversal direction ───────────────────────────────────────
        # Tick-level inference: sweep side first, then the sign of net displacement.
        # With candle_setup_gate on (the default) the gate above guarantees a setup
        # is active, so this only ever serves as the disagreement diagnostic. With
        # the gate OFF it is the sole source of direction, which is the configuration
        # the velocity-spike tests exercise.
        tick_direction = ""
        if is_sweep:
            # Sweeping support -> expect BUY; sweeping resistance -> expect SELL
            sweep_lvl = liq.active_sweeps[0]
            if sweep_lvl.direction == "support":
                tick_direction = "BUY"
            elif sweep_lvl.direction == "resistance":
                tick_direction = "SELL"
            else:
                # Direction not synced: a sweep above a level is a top -> SELL
                tick_direction = "SELL" if price > sweep_lvl.price else "BUY"
        elif is_climax:
            # Bullish climax (net displacement positive) -> expect SELL reversal
            if disp.net_displacement_pips > 0:
                tick_direction = "SELL"
            elif disp.net_displacement_pips < 0:
                tick_direction = "BUY"

        # A confirmed candle setup outranks the tick reading (structural authority).
        if candle_setup_active and candle_setup_direction:
            if tick_direction and tick_direction != candle_setup_direction:
                logger.info(
                    "EntryIDLE: Tick direction=%s disagrees with candle setup direction=%s — using candle setup.",
                    tick_direction, candle_setup_direction
                )
            direction = candle_setup_direction
        else:
            direction = tick_direction

        # A confirmed candle setup stands in for the tick-level spike. Kept because
        # the M15 close is the structural evidence; the tick spike is corroboration
        # we do not require. Named honestly rather than assigned into is_climax,
        # which previously made the ANOMALY log claim a climax that never happened.
        setup_is_evidence = bool(candle_setup_active and direction)

        if (is_climax or is_sweep or setup_is_evidence) and direction:
            self._anomaly_time = ts
            # Anchor to the S/R level the candle setup formed at, not to whatever
            # tick price happened to be current when the setup was first seen.
            # `dist` in _evaluate_arming and _evaluate_retest_wait is measured off
            # this, so anchoring it to an arbitrary tick made the breakaway and
            # retest checks reference a level that was never structurally
            # meaningful. Falls back to `price` when no level is available.
            self._anomaly_price = candle_setup_level if candle_setup_level > 0.0 else price
            self._anomaly_direction = direction
            self._anomaly_type = "sweep" if is_sweep else ("climax" if is_climax else "setup")
            self._max_adverse_excursion = 0.0
            # Seed the observed extreme from the live tick. This is what the breakout
            # fast-kills compare against; _anomaly_price above is the structural level
            # the retest distance is measured from. They are different references and
            # conflating them is what made the kills fire on ordinary retest wicks.
            self._anomaly_extreme_price = price

            if is_sweep:
                reason = "Sweep detected"
            elif is_climax:
                reason = "Microstructure climax"
            else:
                reason = "Candle setup confirmed (no tick-level spike)"
            logger.info(
                "EntryStateMachine ANOMALY detected: price=%.5f level=%.5f direction=%s type=%s (%s; vel_unusual=%s eff=%.2f)",
                price, self._anomaly_price, direction, self._anomaly_type, reason,
                vel.is_unusual, vel.tick_efficiency
            )
            self._transition(STATE_ANOMALY, f"{reason}. Expected reversal: {direction}")


    def _evaluate_anomaly(
        self, price: float, ts: float, vel: NormalizedVelocity, disp: DisplacementState
    ) -> None:
        """Wait for the anomaly to form a trap or show absorption."""
        # Update adverse excursion
        dist = (price - self._anomaly_price) / self._pip
        if self._anomaly_direction == "SELL" and dist > self._max_adverse_excursion:
            self._max_adverse_excursion = dist
        elif self._anomaly_direction == "BUY" and -dist > self._max_adverse_excursion:
            self._max_adverse_excursion = -dist

        # Invalidate if it pushes too far against us without absorption
        if self._max_adverse_excursion > self._mae_invalidate_pips and disp.classification == DISPLACEMENT_IMPULSE:
            self._transition(STATE_INVALIDATED, "Anomaly broken by strong impulse")
            return

        # Arm if we see trap or absorption logic
        if disp.classification in (DISPLACEMENT_TRAP, DISPLACEMENT_ABSORPTION):
            self._arm_start_time = ts
            self._transition(STATE_ARMING, "Absorption confirmed. Arming trigger.")
        # Or arm if velocity completely decays (exhaustion)
        elif vel.is_decaying:
            self._arm_start_time = ts
            self._transition(STATE_ARMING, "Velocity decayed. Arming trigger.")

    def _extreme_breach_pips(self, price: float) -> float:
        """Pips by which `price` has pushed BEYOND the observed extreme. 0.0 if it has not.

        A SELL setup breaks upward and a BUY setup breaks downward. Returns 0.0 while
        the extreme is unseeded so a missing seed can never manufacture a breakout.
        """
        if self._anomaly_extreme_price <= 0.0:
            return 0.0
        if self._anomaly_direction == "SELL":
            return (price - self._anomaly_extreme_price) / self._pip
        if self._anomaly_direction == "BUY":
            return (self._anomaly_extreme_price - price) / self._pip
        return 0.0

    def _track_extreme(self, price: float) -> None:
        """Extend the observed extreme in the setup's break direction."""
        if self._anomaly_extreme_price <= 0.0:
            self._anomaly_extreme_price = price
        elif self._anomaly_direction == "SELL":
            self._anomaly_extreme_price = max(self._anomaly_extreme_price, price)
        elif self._anomaly_direction == "BUY":
            self._anomaly_extreme_price = min(self._anomaly_extreme_price, price)

    def _evaluate_arming(
        self, price: float, ts: float, disp: DisplacementState,
        vel: Optional[NormalizedVelocity] = None,
    ) -> None:
        """Wait for the price to break away from the trap in our direction."""
        dist = (price - self._anomaly_price) / self._pip

        # ── Breakout fast-kill ────────────────────────────────────────────────
        # Measured against the observed extreme, not against _anomaly_price. Those
        # were the same number back when the anomaly was anchored to the live tick;
        # now that it is anchored to the structural level, only the extreme answers
        # the question this check is asking ("did price make a NEW extreme?").
        # Check before extending the extreme, or the extreme absorbs every breach
        # and the kill can never fire.
        fast_kill_buffer = float(self._config.get("entry_fast_kill_buffer_pips", 2.0))
        breach = self._extreme_breach_pips(price)
        if breach > fast_kill_buffer:
            side = "high" if self._anomaly_direction == "SELL" else "low"
            self._transition(
                STATE_INVALIDATED,
                f"New {side} extreme ({breach:+.2f} pips beyond, buffer={fast_kill_buffer:.1f}). Breakout.")
            return
        self._track_extreme(price)

        # ── OPTIMISTIC VELOCITY DECAY TRIGGER ────────────────────────────────
        # If tick velocity is clearly decaying (momentum exhaustion at the peak),
        # trigger immediately at the wick extreme — don't wait for a full breakaway.
        # This is the "sniper entry" — entering right at the inflection point.
        # Gated by config, DISABLED by default 2026-07-23. This path never reads
        # `dist` — no breakaway, no location check, no retest — so it entered at
        # whatever price the tick happened to be at. Measured over 3,919 trigger
        # events across four FX pairs, 98-99% of all entries came straight from
        # ARMING rather than through RETEST_WAIT (only 57 total, 1.5%, were real
        # retests), and this path is the reason. Its gate is also not independent
        # of `strong_reversal` below: `is_decaying and decay<0.5` fires at
        # byte-identical frequency to `disp_class == EXHAUSTION` on every symbol
        # (EUR 10.3%, GBP 20.1%, AUD 7.6%, JPY 11.9%), so the two "confirmations"
        # are one event. Set entry_enable_optimistic_decay_trigger=True to restore.
        if vel is not None and self._config.get("entry_enable_optimistic_decay_trigger", False):
            decay = _num(vel, "decay_ratio", 1.0)
            is_decaying = getattr(vel, "is_decaying", False)
            if is_decaying and decay < 0.5:
                # Stall delay validation
                if (ts - self._arm_start_time) >= self._min_stall_duration:
                    self._transition(
                        STATE_TRIGGERED,
                        f"Optimistic velocity decay entry (decay={decay:.2f} < 0.5, at wick extreme)."
                    )
                    return
                else:
                    logger.debug("EntryARMING: Stall delay active on optimistic trigger. Spent %.1fs / %.1fs", ts - self._arm_start_time, self._min_stall_duration)

        # Trigger criteria: Impulse, or high displacement ratio, or exhaustion (velocity decay = momentum shift)
        is_impulse = (
            (disp.classification == DISPLACEMENT_IMPULSE) or
            (disp.displacement_ratio > 0.5) or
            (disp.classification == "EXHAUSTION")  # Velocity decay = potential reversal
        )

        is_trigger = False
        # Dynamic spread-aware trigger: max(0.5, 1.5 * smoothed_spread)
        trigger_pips = max(0.5, 1.5 * self._smoothed_spread_pips)
        # Requiring `is_impulse` here forced the trigger to wait until the move was
        # actively impulsive — i.e. maximally extended — before entering. Measured
        # over 49 matched FX trades: BUYs filled at median range_pos 0.60 and SELLs
        # at 0.27, buying tops and selling bottoms, with the extreme cohort showing
        # avgMFE 2.1p vs 3.2p for the rest. A plain breakaway is still routed through
        # RETEST_WAIT below (which requires price to travel away and then come back
        # toward the level), so dropping this requirement improves fill location
        # rather than loosening the gate. Set entry_require_impulse_trigger=True to
        # restore the previous behaviour.
        require_impulse = self._config.get("entry_require_impulse_trigger", False)
        impulse_ok = is_impulse or not require_impulse
        if self._anomaly_direction == "SELL" and dist < -trigger_pips and impulse_ok:
            is_trigger = True
        elif self._anomaly_direction == "BUY" and dist > trigger_pips and impulse_ok:
            is_trigger = True

        # Displacement-flip detector: net displacement flipping toward our
        # expected reversal direction is a direct reversal-inception tell.
        net = getattr(disp, "net_displacement_pips", 0.0) or 0.0
        net_sign = 1 if net > 0 else (-1 if net < 0 else 0)
        flip_favorable = (
            (self._anomaly_direction == "BUY" and self._prev_net_sign < 0 and net_sign > 0) or
            (self._anomaly_direction == "SELL" and self._prev_net_sign > 0 and net_sign < 0)
        )
        self._prev_net_sign = net_sign if net_sign != 0 else self._prev_net_sign

        # A clear exhaustion signature (velocity already decayed at the extreme) or
        # a favorable displacement flip is the actual reversal inflection -> trigger
        # now. A plain-impulse/ratio breakaway is a breakout, not yet a confirmed
        # reversal: route it through RETEST_WAIT for velocity-decay confirmation.
        strong_reversal = (disp.classification == "EXHAUSTION") or getattr(disp, "is_exhausting", False) or flip_favorable

        # Debug logging for trigger condition
        logger.info(
            "EntryStateMachine ARMING check: dir=%s dist=%.2f is_impulse=%s (class=%s ratio=%.2f) flip=%s strong=%s trigger=%s",
            self._anomaly_direction, dist, is_impulse, disp.classification, disp.displacement_ratio, flip_favorable, strong_reversal, is_trigger
        )

        if is_trigger:
            if (ts - self._arm_start_time) < self._min_stall_duration:
                logger.debug("EntryARMING: Stall delay active on standard trigger. Spent %.1fs / %.1fs", ts - self._arm_start_time, self._min_stall_duration)
                return
            # `strong_reversal` used to short-circuit straight to TRIGGERED here.
            # It is true on EXHAUSTION, which is the SAME event as the optimistic
            # decay gate above, so it was the second of the two bypasses that kept
            # 98-99% of entries out of RETEST_WAIT. A reversal tell at the level is
            # a reason to WAIT for the retest, not to skip it — the pull-away /
            # come-back check in _evaluate_retest_wait is what distinguishes a
            # genuine retest from a stall part-way down the initial impulse.
            if not self._require_retest_confirm:
                self._transition(STATE_TRIGGERED, f"Reversal inflection confirmed ({disp.classification}, flip={flip_favorable}). Trigger.")
            else:
                self._retest_start_time = ts
                self._retest_extreme = 0.0
                self._transition(STATE_RETEST_WAIT, f"Breakaway impulse ({disp.classification}); awaiting velocity-decay retest confirmation.")

    def _evaluate_retest_wait(
        self, price: float, ts: float, velocity: NormalizedVelocity
    ) -> None:
        """Wait for price to test the anomaly level and verify if opposing flow is exhausted."""
        dist = (price - self._anomaly_price) / self._pip

        # 1. Beyond-extreme fast-kill.
        # Was `dist > 0.0` against _anomaly_price with no buffer at all, while ARMING
        # allowed entry_fast_kill_buffer_pips against the same reference. Since
        # _anomaly_price is the structural level, that read as "price is back on the
        # wrong side of the level" — which is what a retest LOOKS like as it returns,
        # so a wick a fraction of a pip through the level killed a valid setup. Now
        # measured against the observed extreme, with its own buffer.
        break_buffer = float(self._config.get(
            "entry_retest_break_buffer_pips",
            self._config.get("entry_fast_kill_buffer_pips", 2.0)))
        breach = self._extreme_breach_pips(price)
        if breach > break_buffer:
            side = "high" if self._anomaly_direction == "SELL" else "low"
            self._retest_outcomes["breakout"] = self._retest_outcomes.get("breakout", 0) + 1
            self._transition(
                STATE_INVALIDATED,
                f"New {side} extreme ({breach:+.2f} pips beyond, buffer={break_buffer:.1f}) during retest. Breakout.")
            return
        self._track_extreme(price)

        # 2. Timeout. Configurable: with both ARMING bypasses closed, RETEST_WAIT is
        # the only path to an entry, so this value now sets the trade rate outright.
        elapsed = ts - self._retest_start_time
        if elapsed > self._retest_timeout_sec:
            self._retest_outcomes["timeout"] = self._retest_outcomes.get("timeout", 0) + 1
            logger.info(
                "EntryRETEST timed out after %.1fs (limit %.1fs, closest approach %.1fp of zone). Outcomes so far: %s",
                elapsed, self._retest_timeout_sec, self._retest_extreme, self._retest_outcomes)
            self._transition(STATE_INVALIDATED, f"Retest timed out after {elapsed:.1f}s")
            return

        # 3. Check if price is within the retest zone (dynamic zone size via vol_pips)
        vol_pips = getattr(velocity, "vol_pips", 3.0)
        zone_width = max(2.0, vol_pips)
        in_zone = abs(dist) <= zone_width

        # Track the furthest reversal-side excursion so far. For a SELL the reversal
        # side is below the anomaly top (dist < 0); for a BUY it is above the low.
        if self._anomaly_direction == "SELL":
            self._retest_extreme = min(self._retest_extreme, dist)
        else:
            self._retest_extreme = max(self._retest_extreme, dist)

        # Directional-approach gate. The old symmetric `abs(dist) <= zone_width` fired on
        # the FIRST momentum stall anywhere in the zone — including a stall part-way down
        # the initial impulse — so a SELL could trigger at the bottom of the drop (and a
        # BUY at the top of the pop). A genuine retest is: price pulls AWAY from the level,
        # then comes BACK toward it and fails. Require both before allowing the trigger.
        require_approach = self._config.get("entry_retest_require_approach", True)
        approached = True
        if require_approach:
            pulled_away = abs(self._retest_extreme) >= zone_width
            if self._anomaly_direction == "SELL":
                # price must be rallying back UP toward the top, off its retest low
                coming_back = dist > self._retest_extreme + 0.2 * zone_width
            else:
                # price must be falling back DOWN toward the low, off its retest high
                coming_back = dist < self._retest_extreme - 0.2 * zone_width
            approached = pulled_away and coming_back

        if in_zone:
            decay = _num(velocity, "decay_ratio", 1.0)

            # Trigger invalidate: high momentum on retest (breaking through)
            if decay > 0.8:
                self._retest_outcomes["high_momentum"] = self._retest_outcomes.get("high_momentum", 0) + 1
                self._transition(STATE_INVALIDATED, f"High momentum on retest (decay={decay:.2f}). Breakout threat.")
                return

            # Trigger confirm: relaxed to just decay < 0.6 (candle setup already filtered noise)
            # Old: decay < 0.4 AND tr_10 < tr_300 * 0.6 (dual condition — too strict)
            if decay < 0.6 and approached:
                if (ts - self._arm_start_time) >= self._min_stall_duration:
                    self._retest_outcomes["confirmed"] = self._retest_outcomes.get("confirmed", 0) + 1
                    self._transition(STATE_TRIGGERED, f"Retest confirmed (decay={decay:.2f}, dist={dist:+.1f}p off extreme {self._retest_extreme:+.1f}p). Reversal trigger.")
                else:
                    logger.debug("EntryRETEST: Stall delay active on retest trigger. Spent %.1fs / %.1fs", ts - self._arm_start_time, self._min_stall_duration)
            elif decay < 0.6 and not approached:
                logger.debug("EntryRETEST: decay ok but no directional retest yet (dist=%.1fp extreme=%.1fp). Awaiting return to level.", dist, self._retest_extreme)


    def _calculate_quality(self, regime: "RegimeState", mtf: MTFState) -> float:
        """Calculate a 0.0 to 1.0 confidence score for the entry."""
        score = 0.5 # Base
        
        # Determine dominant regime label
        dom_regime = getattr(regime, "dominant_regime", getattr(regime, "regime", "ranging"))
        
        # Regime alignment
        if dom_regime in ("reversal", "exhaustion") or dom_regime in (self.REGIME_REVERSAL, self.REGIME_EXHAUSTION):
            score += 0.2
        elif dom_regime in ("ranging", "ranging_chop", "ranging_flat") or dom_regime == self.REGIME_RANGE_CHOP:
            score += 0.1
        elif dom_regime in ("breakout", "panic"):
            # Penalize quality in expansion states (requiring higher consensus for entry)
            score -= 0.15
            
        # MTF Context
        if self._anomaly_direction == "BUY" and mtf.alignment_score > 0.3:
            score += 0.2
        elif self._anomaly_direction == "SELL" and mtf.alignment_score < -0.3:
            score += 0.2
        elif mtf.is_pullback:
            score += 0.1 # Reversing a pullback into the main trend is good
            
        return min(max(score, 0.0), 1.0)


__all__ = [
    "EntryStateMachine",
    "EntryDecision",
    "STATE_IDLE",
    "STATE_ANOMALY",
    "STATE_ARMING",
    "STATE_RETEST_WAIT",
    "STATE_TRIGGERED",
    "STATE_INVALIDATED",
]
