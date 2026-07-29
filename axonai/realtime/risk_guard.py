"""Risk management and drawdown circuit breaker for AxonAI.

Monitors daily profit/loss and disables order placement if limits are exceeded.
"""

import os
import json
import logging
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class RiskGuard:
    """Drawdown Circuit Breaker.
    
    Tracks daily profit/loss and halts all execution if daily drawdown exceeds threshold.
    """

    def __init__(self, config: dict):
        self.config = config
        self.max_daily_drawdown_pct = config.get("risk_max_daily_drawdown_pct", 5.0)  # default 5%
        self.max_daily_loss_amount = config.get("risk_max_daily_loss_amount", 500.0) # default $500
        self.current_equity = 0.0

        # ── Prop-firm compliance layer (off unless prop_guard_enabled) ────────
        # Models the two limits that actually kill a funded account and which the
        # plain daily breaker above does not: an OVERALL drawdown floor from the
        # initial balance, and a daily limit keyed to the BROKER SERVER day.
        self.prop_enabled = bool(config.get("prop_guard_enabled", False))
        self.max_total_drawdown_pct = float(config.get("prop_max_drawdown_pct", 10.0))
        self.trailing_drawdown = bool(config.get("prop_max_drawdown_trailing", False))
        self.prop_daily_loss_pct = float(config.get("prop_daily_loss_pct", 5.0))
        self.flatten_on_breach = bool(config.get("prop_flatten_on_breach", True))
        buf = float(config.get("prop_safety_buffer_pct", 20.0))
        # Trip at (100-buffer)% of each limit so the real breach line is never hit.
        self.safety_factor = max(0.0, min(1.0, 1.0 - buf / 100.0))
        # Consistency rule (payout gate — NOT a hard breach; blocks new entries
        # but never flattens, since flattening would only enlarge the day).
        self.consistency_pct = float(config.get("prop_consistency_pct", 0.0))
        # Profit target: informational one-shot log line (does not halt trading).
        self.profit_target_pct = float(config.get("prop_profit_target_pct", 0.0))
        self._target_hit_logged = False
        self.prop_state_file = config.get("prop_state_file", "reports/prop_guard.json")
        self.breach_reason = ""
        self.prop_state = self._load_prop_state() if self.prop_enabled else {}

        # A prop account gets its OWN daily-PnL file. The default path is shared,
        # CWD-relative state; if a funded process and a non-prop process both used
        # it, each would read the other's starting equity and silently mis-measure
        # (or disable) its daily limit.
        self.risk_pnl_log_file = config.get(
            "risk_pnl_file",
            "reports/daily_pnl_prop.json" if self.prop_enabled else "reports/daily_pnl.json",
        )

        # ── Baseline resolution — NEVER inferred from the live balance ─────────
        # Seeding the floor from "whatever the account is worth right now" places
        # it BELOW the firm's real line whenever the guard is armed (or its state
        # file is lost) on a drawn-down account — precisely when protection
        # matters. The baseline must be stated explicitly or restored from disk;
        # if neither is available the guard FAILS CLOSED (see is_halted).
        self.baseline_source = ""
        if self.prop_enabled:
            cfg_initial = config.get("prop_initial_balance")
            persisted = float(self.prop_state.get("initial_balance", 0.0) or 0.0)
            if cfg_initial:
                base = float(cfg_initial)
                if persisted and abs(persisted - base) > 0.01:
                    logger.warning(
                        "RiskGuard: prop initial balance overridden by config: "
                        "persisted %.2f -> %.2f", persisted, base)
                self.prop_state["initial_balance"] = base
                self.baseline_source = "config"
                self._save_prop_state()
            elif persisted > 0.0:
                self.baseline_source = "persisted"
            else:
                logger.critical(
                    "RiskGuard: PROP GUARD ARMED WITHOUT A BASELINE. Trading is "
                    "halted until the account's true starting balance is supplied "
                    "via --prop-initial-balance (or prop_initial_balance). Refusing "
                    "to guess it from the live balance, which would put the "
                    "drawdown floor below the firm's real limit."
                )

        # Load daily PnL
        self.daily_pnl = self._load_daily_pnl()

    # ── day boundary ──────────────────────────────────────────────────────────
    def _today(self) -> str:
        """Current trading day.

        With the prop guard on this is the BROKER SERVER day (what the firm's
        daily limit is measured against); otherwise the local date, preserving
        the original behaviour exactly.
        """
        if not self.prop_enabled:
            return str(date.today())
        try:
            from axonai.dataflows.mt5_data import get_broker_tz_offset
            offset = get_broker_tz_offset()
            return str((datetime.now(timezone.utc) + timedelta(hours=offset)).date())
        except Exception:
            return str(date.today())

    # ── prop-firm state (survives the daily reset) ────────────────────────────
    def _load_prop_state(self) -> dict:
        if os.path.exists(self.prop_state_file):
            try:
                with open(self.prop_state_file, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
            except Exception as e:
                logger.error("RiskGuard: Failed to load prop guard state: %s", e)
        return {"initial_balance": 0.0, "peak_equity": 0.0}

    def _save_prop_state(self):
        try:
            d = os.path.dirname(self.prop_state_file)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self.prop_state_file, "w") as f:
                json.dump(self.prop_state, f)
        except Exception as e:
            logger.error("RiskGuard: Failed to save prop guard state: %s", e)

    def _load_daily_pnl(self) -> dict:
        if os.path.exists(self.risk_pnl_log_file):
            try:
                with open(self.risk_pnl_log_file, "r") as f:
                    data = json.load(f)
                    if data.get("date") == self._today():
                        return data
            except Exception as e:
                logger.error("RiskGuard: Failed to load daily PnL log: %s", e)
        return {"date": self._today(), "start_equity": 0.0, "realized_pnl": 0.0}

    def _save_daily_pnl(self):
        os.makedirs(os.path.dirname(self.risk_pnl_log_file), exist_ok=True)
        try:
            with open(self.risk_pnl_log_file, "w") as f:
                json.dump(self.daily_pnl, f)
        except Exception as e:
            logger.error("RiskGuard: Failed to save daily PnL: %s", e)

    def update_equity(self, current_equity: float, current_balance: float):
        """Seed daily starting equity on first call of the day."""
        self.current_equity = current_equity
        self._last_balance = float(current_balance or 0.0)
        today = self._today()
        # Daily baseline: use the HIGHER of equity and balance. If the day's first
        # observation lands after price has already moved (fresh deploy, restart,
        # missing state), the depressed equity would otherwise become the
        # denominator and quietly push the tripwire below the firm's daily line.
        # Erring high is the safe direction — it can only trip earlier.
        day_open = max(float(current_equity or 0.0), float(current_balance or 0.0))
        if self.daily_pnl["date"] != today:
            self.daily_pnl = {"date": today, "start_equity": day_open, "realized_pnl": 0.0}
            self._save_daily_pnl()
        elif self.daily_pnl["start_equity"] == 0.0:
            self.daily_pnl["start_equity"] = day_open
            self._save_daily_pnl()

        # Prop layer: ratchet the peak equity (only meaningful for a trailing
        # floor, but always tracked so a static account can be switched to
        # trailing without losing history). The initial balance is NEVER seeded
        # here — see the baseline resolution in __init__.
        if self.prop_enabled and self.baseline_source:
            if current_equity > float(self.prop_state.get("peak_equity", 0.0) or 0.0):
                self.prop_state["peak_equity"] = current_equity
                self._save_prop_state()

    def record_trade_result(self, pnl: float):
        """Update realized PnL for the day."""
        today = self._today()
        if self.daily_pnl["date"] != today:
            self.daily_pnl = {"date": today, "start_equity": 0.0, "realized_pnl": 0.0}

        self.daily_pnl["realized_pnl"] += pnl
        self._save_daily_pnl()
        # Ratchet the phase's best single-day realized profit for the consistency
        # check. Only positive days count — a losing day never sets the payout
        # gate. Persists in prop_state so it survives across restarts.
        if self.prop_enabled:
            today_pnl = float(self.daily_pnl["realized_pnl"])
            if today_pnl > 0:
                best = float(self.prop_state.get("best_day_pnl", 0.0) or 0.0)
                if today_pnl > best:
                    self.prop_state["best_day_pnl"] = today_pnl
                    self._save_prop_state()

    # ── prop-firm limits ──────────────────────────────────────────────────────
    def drawdown_floor(self) -> float:
        """Absolute equity level that must never be reached (0.0 = not armed).

        Static: ``initial_balance × (1 - max_dd%)``. Trailing: the same distance
        below the highest equity ever seen, which ratchets up and never back down.
        The safety buffer pulls the tripwire above the firm's real line.
        """
        if not self.prop_enabled:
            return 0.0
        initial = float(self.prop_state.get("initial_balance", 0.0) or 0.0)
        if initial <= 0.0:
            return 0.0
        effective_pct = self.max_total_drawdown_pct * self.safety_factor
        if self.trailing_drawdown:
            peak = max(float(self.prop_state.get("peak_equity", 0.0) or 0.0), initial)
            return peak * (1.0 - effective_pct / 100.0)
        return initial * (1.0 - effective_pct / 100.0)

    def hard_floor(self) -> float:
        """The firm's actual breach line, without the safety buffer (0.0 = n/a)."""
        if not self.prop_enabled:
            return 0.0
        initial = float(self.prop_state.get("initial_balance", 0.0) or 0.0)
        if initial <= 0.0:
            return 0.0
        if self.trailing_drawdown:
            peak = max(float(self.prop_state.get("peak_equity", 0.0) or 0.0), initial)
            return peak * (1.0 - self.max_total_drawdown_pct / 100.0)
        return initial * (1.0 - self.max_total_drawdown_pct / 100.0)

    def is_halted(self, current_equity: float) -> tuple[bool, str]:
        """Check if circuit breaker has tripped."""
        # Prop-firm guard armed with no known baseline: FAIL CLOSED. Trading
        # unprotected on a funded account is worse than not trading at all.
        if self.prop_enabled and not self.baseline_source:
            msg = ("PROP GUARD HALTED: no starting balance known — pass "
                   "--prop-initial-balance (e.g. 100000) to arm the drawdown floor")
            self.breach_reason = msg
            return True, msg

        # Prop-firm OVERALL drawdown floor. Checked first and independently of the
        # daily state: it spans the whole account life, so a slow multi-day bleed
        # (which no daily check can ever see) still trips it.
        if self.prop_enabled:
            floor = self.drawdown_floor()
            if floor > 0.0 and current_equity <= floor:
                msg = (f"MAX DRAWDOWN breach: equity {current_equity:.2f} <= "
                       f"floor {floor:.2f} (firm's line {self.hard_floor():.2f}, "
                       f"{'trailing' if self.trailing_drawdown else 'static'} "
                       f"{self.max_total_drawdown_pct}%)")
                logger.critical("RiskGuard: %s", msg)
                self.breach_reason = msg
                return True, msg

        if self.daily_pnl["date"] != self._today():
            return False, ""

        start_eq = self.daily_pnl["start_equity"]
        if start_eq == 0.0:
            return False, ""

        # Sanity-check the daily baseline against the account it is measuring.
        # A baseline wildly out of scale means the file belongs to a different
        # account (shared/stale state): fall back to the live equity rather than
        # trusting a number that would silently disable the daily limit.
        if self.prop_enabled and current_equity > 0.0:
            if start_eq < current_equity * 0.5 or start_eq > current_equity * 2.0:
                logger.warning(
                    "RiskGuard: implausible daily baseline %.2f for equity %.2f "
                    "(stale or foreign state file) — reseeding to current equity",
                    start_eq, current_equity)
                start_eq = current_equity
                self.daily_pnl["start_equity"] = current_equity
                self._save_daily_pnl()

        # Drawdown calculation (unrealized + realized)
        floating_loss = start_eq - current_equity
        realized_loss = -self.daily_pnl["realized_pnl"]
        
        # Absolute drawdown pct
        drawdown_pct = (floating_loss / start_eq) * 100.0 if start_eq > 0 else 0.0

        # Prop-firm DAILY loss limit, measured on the broker server day and
        # against the firm's own percentage (with the safety buffer applied).
        if self.prop_enabled:
            daily_limit = self.prop_daily_loss_pct * self.safety_factor
            if drawdown_pct >= daily_limit:
                msg = (f"DAILY LOSS breach: {drawdown_pct:.2f}% >= {daily_limit:.2f}% "
                       f"(firm's limit {self.prop_daily_loss_pct}%; "
                       f"{floating_loss:.2f} from {start_eq:.2f})")
                logger.critical("RiskGuard: %s", msg)
                self.breach_reason = msg
                return True, msg
            # Prop limits are AUTHORITATIVE: the legacy checks below default to
            # 5% / $500, which on a funded six-figure account would trip on a
            # routine drawdown and mask the real limits. Skip them entirely.
            return False, ""

        # Check against daily percentage limit
        if drawdown_pct >= self.max_daily_drawdown_pct:
            msg = f"Daily drawdown ({drawdown_pct:.2f}%) exceeds limit ({self.max_daily_drawdown_pct}%)"
            logger.warning("RiskGuard: %s", msg)
            return True, msg
            
        # Check against daily amount limit (combined floating + realized)
        total_loss = floating_loss + realized_loss
        if total_loss >= self.max_daily_loss_amount:
            msg = f"Daily loss (${total_loss:.2f}) exceeds limit (${self.max_daily_loss_amount:.2f})"
            logger.warning("RiskGuard: %s", msg)
            return True, msg
            
        return False, ""

    # ── consistency rule (payout gate; blocks new entries, never flattens) ───
    def _consistency_blocked(self, current_balance: float) -> tuple[bool, str]:
        """Would opening a NEW entry today risk the 45% consistency rule?

        The rule: no single trading day may exceed X% of total realized profit.
        We block new entries once today's realized ratio crosses the buffered
        threshold, so an in-progress winning day cannot inflate further and lock
        payout. Never flattens — flattening only enlarges today's realized.
        """
        if not self.prop_enabled or self.consistency_pct <= 0:
            return False, ""
        initial = float(self.prop_state.get("initial_balance", 0.0) or 0.0)
        if initial <= 0 or current_balance <= 0:
            return False, ""
        total_profit = float(current_balance) - initial
        today_pnl = float(self.daily_pnl.get("realized_pnl", 0.0) or 0.0)
        best = float(self.prop_state.get("best_day_pnl", 0.0) or 0.0)
        worst_day = max(today_pnl, best)
        # Only meaningful once there is positive net profit and a positive day.
        if total_profit <= 0 or worst_day <= 0:
            return False, ""
        threshold = self.consistency_pct * self.safety_factor
        ratio = 100.0 * worst_day / total_profit
        if ratio >= threshold:
            return True, (
                f"CONSISTENCY block: worst-day {worst_day:+.2f} is {ratio:.1f}% "
                f"of total profit {total_profit:+.2f} (threshold {threshold:.1f}%, "
                f"firm's rule {self.consistency_pct:.0f}%) — new entries blocked "
                f"for the day; open positions untouched")
        return False, ""

    def _check_profit_target(self, current_equity: float) -> None:
        """One-shot INFO log when the phase profit target is reached (no halt)."""
        if not self.prop_enabled or self.profit_target_pct <= 0 or self._target_hit_logged:
            return
        initial = float(self.prop_state.get("initial_balance", 0.0) or 0.0)
        if initial <= 0:
            return
        target = initial * (1.0 + self.profit_target_pct / 100.0)
        if current_equity >= target:
            self._target_hit_logged = True
            logger.info(
                "PROFIT TARGET REACHED: equity %.2f >= %.2f (%.1f%% of %.0f "
                "initial). Phase 1 pass condition met; continuing to trade — "
                "watch the consistency rule.",
                current_equity, target, self.profit_target_pct, initial)

    def entry_allowed(self, current_equity: float, current_balance: float) -> tuple[bool, str]:
        """Gate for NEW positions: hard halts + payout-gate rules.

        Returns (True, "") when a new entry may proceed; (False, reason) otherwise.
        Hard halts (DD floor / daily loss) go through is_halted so the daemon's
        flatten-on-breach path still fires; consistency is added on top here as
        a block-only rule (never flatten).
        """
        halted, reason = self.is_halted(current_equity)
        if halted:
            return False, reason
        self._check_profit_target(current_equity)
        blocked, why = self._consistency_blocked(current_balance)
        if blocked:
            return False, why
        return True, ""

    @property
    def is_tripped(self) -> bool:
        """Helper checking if new orders should be REJECTED (hard halt OR payout gate).

        This is the property the executor consults before placing an order, so it
        must be the union of "hard breach" (is_halted) AND "payout gate" (consistency).
        Flatten decisions in the daemon separately query is_halted() directly so
        the consistency rule never triggers a mass-close.
        """
        if not hasattr(self, "current_equity") or self.current_equity == 0.0:
            return False
        halted, _ = self.is_halted(self.current_equity)
        if halted:
            return True
        # Consistency needs the current balance; fall back to equity when the
        # daemon hasn't fed a fresh balance in (rare — update_equity is called
        # each tick). Ratio will still be meaningful.
        cb = getattr(self, "_last_balance", 0.0) or self.current_equity
        blocked, _ = self._consistency_blocked(cb)
        return blocked
