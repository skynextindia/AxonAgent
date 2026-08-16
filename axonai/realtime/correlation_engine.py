"""Cross-pair correlation engine (EURUSD ↔ USDJPY, extensible to more pairs).

One shared instance owned by the DaemonSupervisor; every per-pair daemon holds a
reference and calls :meth:`evaluate_entry` before opening a position. All shared
state is guarded by a single RLock. Four behaviors:

  (a) correlated-exposure cap  — cap combined net-USD exposure across pairs
  (b) vol-ratio calibration    — per-pair stop floor from realized H1 volatility
  (c) signal confirmation/veto — block a USD-direction entry the lead pair contradicts
  (d) live size scaling        — shrink the follower's lot when |correlation| is high

(a)(c)(d) run in evaluate_entry(); (b) is exposed via calibrated_overrides() and
applied by the supervisor when it constructs each daemon.

Top-level imports are stdlib-only; MetaTrader5 and symbol mapping are imported
lazily inside the fetch path so the decision logic stays unit-testable offline.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

_CONTRACT = 100000.0


def _canon(symbol: str) -> str:
    letters = "".join(c for c in (symbol or "").upper() if c.isalpha())
    return letters[:6] if len(letters) >= 6 else letters


def _pip_size(symbol: str) -> float:
    s = (symbol or "").upper()
    return 0.01 if ("JPY" in s or "XAU" in s) else 0.0001


def position_usd(symbol: str, direction: str, lot: float, price: float) -> float:
    """Signed USD notional of a position (long-USD positive).

    XXXUSD (quote USD): long pair = short USD  → -lot*contract*price
    USDXXX (base USD):  long pair = long USD   → +lot*contract
    crosses (no USD leg): 0 (ignored for USD netting).
    """
    c = _canon(symbol)
    base, quote = c[:3], c[3:6]
    side = 1.0 if str(direction).upper().startswith("B") else -1.0
    if quote == "USD":
        return -side * lot * _CONTRACT * (price or 1.0)
    if base == "USD":
        return side * lot * _CONTRACT
    return 0.0


def _returns(closes: List[float]) -> List[float]:
    return [(closes[i] - closes[i - 1]) / (closes[i - 1] or 1.0) for i in range(1, len(closes))]


def pearson(a: List[float], b: List[float]) -> float:
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    if da == 0 or db == 0:
        return 0.0
    return max(-1.0, min(1.0, num / (da * db)))


class CorrelationEngine:
    def __init__(self, symbols, config, lead_symbol=None):
        self.config = config or {}
        self.symbols = [_canon(s) for s in symbols]
        self.lead = _canon(lead_symbol or self.config.get("corr_lead_symbol", "EURUSD"))
        self.window = int(self.config.get("corr_window_bars", 100))
        self.refresh_seconds = float(self.config.get("corr_refresh_seconds", 300))
        self.max_net_usd = float(self.config.get("corr_max_net_usd", 200000.0))
        self.bias_lookback = int(self.config.get("corr_bias_lookback_bars", 10))
        self.bias_threshold = float(self.config.get("corr_veto_bias_threshold", 0.0015))
        self.bias_veto_enabled = bool(self.config.get("corr_bias_veto_enabled", True))
        self.size_scale_min = float(self.config.get("corr_size_scale_min", 0.25))

        self._lock = threading.RLock()
        self._positions: Dict[int, Tuple[str, str, float, float]] = {}  # ticket -> (sym, dir, lot, price)
        # In-flight entry reservations (canon symbol -> direction). One daemon thread
        # per pair evaluates concurrently, and a position is only registered AFTER its
        # order fills — so two conflicting-USD entries could both pass evaluate_entry
        # before either registered (observed 2026-08-13: EURUSD+USDJPY both SELL 1.3s
        # apart). reserve_entry() re-checks + reserves under the lock so the 2nd thread
        # sees the 1st's in-flight entry and is vetoed. Cleared by register_position
        # (fill) or release_pending (fill failed). In-memory: a restart clears it.
        self._pending: Dict[str, str] = {}
        self.rolling_corr = 0.0
        self.lead_bias = 0.0             # signed recent return of the lead pair
        self.atr_pips: Dict[str, float] = {}
        self.atr_vol_ratio = 1.0         # follower / lead realized-vol ratio
        self._last_refresh = 0.0

        # Best-effort initial calibration (safe no-op when MT5 is unavailable).
        try:
            self._refresh(force=True)
        except Exception as e:
            logger.debug("CorrelationEngine: initial refresh skipped: %s", e)

    # ── position tracking ─────────────────────────────────────────────────────
    def register_position(self, symbol, direction, lot, price, ticket) -> None:
        with self._lock:
            self._positions[int(ticket)] = (_canon(symbol), str(direction), float(lot), float(price))
            self._pending.pop(_canon(symbol), None)   # fill confirmed → clear the reservation

    def unregister_position(self, ticket) -> None:
        with self._lock:
            self._positions.pop(int(ticket), None)

    @property
    def net_usd_exposure(self) -> float:
        with self._lock:
            return sum(position_usd(s, d, l, p) for (s, d, l, p) in self._positions.values())

    # ── data refresh ──────────────────────────────────────────────────────────
    def _fetch_h1_closes(self, broker_symbol, n):
        try:
            import MetaTrader5 as mt5
        except Exception:
            return []
        if not mt5 or not mt5.terminal_info():
            return []
        try:
            info = mt5.symbol_info(broker_symbol)
            if info is not None and not info.visible:
                mt5.symbol_select(broker_symbol, True)
            rates = mt5.copy_rates_from_pos(broker_symbol, mt5.TIMEFRAME_H1, 0, n)
        except Exception:
            return []
        if rates is None:
            return []
        try:
            return [float(r["close"]) for r in rates]
        except Exception:
            return []

    def _broker_symbol(self, canon):
        try:
            from axonai.dataflows.mt5_data import _to_mt5_symbol
            return _to_mt5_symbol(canon, self.config)
        except Exception:
            return canon

    def _refresh(self, force=False):
        now = time.time()
        if not force and (now - self._last_refresh) < self.refresh_seconds:
            return
        closes, atr = {}, {}
        for c in self.symbols:
            cl = self._fetch_h1_closes(self._broker_symbol(c), self.window)
            if cl:
                closes[c] = cl
                atr[c] = self._atr_pips(cl, c)
        with self._lock:
            self._last_refresh = now
            if atr:
                self.atr_pips.update(atr)
            followers = [c for c in self.symbols if c != self.lead]
            if self.lead in closes and followers and followers[0] in closes:
                self.rolling_corr = pearson(_returns(closes[self.lead]), _returns(closes[followers[0]]))
            if self.lead in closes and len(closes[self.lead]) > self.bias_lookback:
                seg = closes[self.lead]
                base = seg[-1 - self.bias_lookback] or 1.0
                self.lead_bias = (seg[-1] - seg[-1 - self.bias_lookback]) / base
            if (followers and self.lead in self.atr_pips
                    and followers[0] in self.atr_pips and self.atr_pips[self.lead]):
                self.atr_vol_ratio = self.atr_pips[followers[0]] / self.atr_pips[self.lead]

    @staticmethod
    def _atr_pips(closes, symbol):
        """Realized volatility proxy: mean absolute bar-to-bar move in pips."""
        if len(closes) < 2:
            return 0.0
        pip = _pip_size(symbol)
        diffs = [abs(closes[i] - closes[i - 1]) / pip for i in range(1, len(closes))]
        return sum(diffs) / len(diffs)

    # ── (b) vol-ratio calibration ──────────────────────────────────────────────
    def calibrated_overrides(self, symbol) -> dict:
        """Config overrides for a pair derived from its realized H1 volatility.

        Sets a data-driven stop floor (min_stop_pips >= 0.5 * realized ATR pips)
        from the pair's OWN measured vol, so the EURUSD↔USDJPY vol ratio is
        reflected automatically. Empty dict when no data is available yet.
        """
        c = _canon(symbol)
        with self._lock:
            ap = self.atr_pips.get(c, 0.0)
        if ap <= 0:
            return {}
        floor = max(float(self.config.get("min_stop_pips", 16.0)), round(0.5 * ap, 1))
        return {"min_stop_pips": floor}

    # ── (a)(c)(d) entry decision ───────────────────────────────────────────────
    def evaluate_entry(self, symbol, direction, live_state=None, live_evidence=None) -> Tuple[bool, float, str]:
        """Return (allow, size_scale, reason) for a proposed entry."""
        if not self.config.get("corr_engine_enabled", True):
            return True, 1.0, "engine disabled"
        try:
            self._refresh()
        except Exception:
            pass

        c = _canon(symbol)

        # Sign of the proposed entry's USD exposure (price-independent).
        entry_usd = position_usd(symbol, direction, 1.0, 1.0)
        entry_sign = 1.0 if entry_usd > 0 else -1.0 if entry_usd < 0 else 0.0

        # (lock) Dollar-direction lock — applies to the LEAD pair too, so the rule
        # never depends on which pair fired first. While ANY position is open, a
        # new entry must AGREE on dollar direction with every open position:
        # negatively-correlated pairs (EURUSD↔USDJPY) must be traded in opposite
        # pair directions, which is the SAME USD direction. Checked per-position
        # (not on the net) so a conflicted legacy book that nets ~0 still blocks a
        # new conflicting entry. position_usd's sign already encodes base/quote
        # inversion, so SELL EURUSD (dollar UP) allows BUY USDJPY, vetoes SELL.
        if self.config.get("corr_require_usd_alignment", True) and entry_sign != 0:
            with self._lock:
                open_positions = list(self._positions.values())
            for (s, d, l, p) in open_positions:
                pu = position_usd(s, d, l, p)
                psign = 1.0 if pu > 0 else -1.0 if pu < 0 else 0.0
                if psign != 0 and psign != entry_sign:
                    edir = "UP" if entry_sign > 0 else "DOWN"
                    odir = "UP" if psign > 0 else "DOWN"
                    return (False, 0.0,
                            f"vetoed: {c} {str(direction).upper()} (dollar {edir}) conflicts "
                            f"with open {s} {str(d).upper()} (dollar {odir})")

        # The lead pair skips the follower-only checks (bias veto / exposure / sizing).
        if c == self.lead:
            return True, 1.0, "lead pair"

        with self._lock:
            corr = self.rolling_corr
            bias = self.lead_bias
            net = sum(position_usd(s, d, l, p) for (s, d, l, p) in self._positions.values())

        # (c) veto: a USD-strengthening entry contradicts a strong up-bias in the
        # lead (EURUSD up = USD weak), and a USD-weakening entry contradicts a
        # strong down-bias (EURUSD down = USD strong).
        if self.bias_veto_enabled and abs(bias) >= self.bias_threshold and entry_sign != 0:
            if (entry_sign > 0 and bias > 0) or (entry_sign < 0 and bias < 0):
                return False, 0.0, f"vetoed: {self.lead} bias {bias:+.4f} contradicts USD direction"

        scale = 1.0
        # (a) exposure cap: shrink/deny when the entry stacks the existing net-USD bet.
        if entry_sign != 0 and net != 0 and entry_sign == (1.0 if net > 0 else -1.0):
            usage = abs(net) / self.max_net_usd if self.max_net_usd > 0 else 0.0
            if usage >= 1.0:
                return False, 0.0, f"vetoed: net USD exposure {net:+.0f} at cap"
            scale *= max(0.0, 1.0 - usage)

        # (d) correlation-based size scaling: higher |corr| → more redundancy → smaller.
        scale *= (1.0 - 0.5 * abs(corr))

        scale = max(self.size_scale_min, min(1.0, scale))
        return True, scale, f"corr={corr:+.2f} net={net:+.0f} scale={scale:.2f}"

    def reserve_entry(self, symbol, direction, live_state=None, live_evidence=None) -> Tuple[bool, float, str]:
        """Atomic check-and-reserve — the race-safe wrapper the daemon must call
        instead of evaluate_entry. Runs the normal evaluation, then re-checks the
        USD-alignment rule against BOTH open positions AND in-flight reservations
        under a single lock, and (if clear) reserves this entry's direction. Two
        follower threads therefore serialize here: the second sees the first's
        pending entry and is vetoed, closing the cross-thread race that let both
        pairs open the same (conflicting-USD) side. The caller MUST, after this
        returns allow=True, later call register_position (on fill) or
        release_pending (on fill failure) — otherwise the reservation leaks and
        blocks that symbol until the next restart.
        """
        allow, scale, reason = self.evaluate_entry(symbol, direction, live_state, live_evidence)
        if not allow:
            return allow, scale, reason
        c = _canon(symbol)
        if not self.config.get("corr_require_usd_alignment", True):
            return allow, scale, reason
        _eu = position_usd(symbol, direction, 1.0, 1.0)
        entry_sign = 1.0 if _eu > 0 else -1.0 if _eu < 0 else 0.0
        if entry_sign == 0:
            return allow, scale, reason
        with self._lock:
            # Re-check under the lock against open positions AND pending reservations.
            candidates = [(s, d) for (s, d, l, p) in self._positions.values()]
            candidates += list(self._pending.items())
            for (s, d) in candidates:
                if _canon(s) == c:
                    continue
                _pu = position_usd(s, d, 1.0, 1.0)
                psign = 1.0 if _pu > 0 else -1.0 if _pu < 0 else 0.0
                if psign != 0 and psign != entry_sign:
                    edir = "UP" if entry_sign > 0 else "DOWN"
                    odir = "UP" if psign > 0 else "DOWN"
                    return (False, 0.0,
                            f"vetoed: {c} {str(direction).upper()} (dollar {edir}) conflicts with "
                            f"open/pending {_canon(s)} {str(d).upper()} (dollar {odir})")
            self._pending[c] = str(direction)   # no conflict → reserve atomically
        return allow, scale, reason

    def release_pending(self, symbol) -> None:
        """Drop an in-flight reservation whose order did NOT fill (execute returned
        nothing or raised), so the symbol isn't blocked by a phantom entry."""
        with self._lock:
            self._pending.pop(_canon(symbol), None)
