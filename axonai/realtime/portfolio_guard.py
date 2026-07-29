"""Portfolio-level pre-trade risk guard.

Account-wide caps enforced before any new order, complementing:
  - the per-(symbol, magic) 1-position cap in MT5TradeExecutor.send_order, and
  - the equity-drawdown RiskGuard (which is equity-seeded and therefore a no-op
    in bridge mode).

Checks (config-driven; each disabled when its limit is 0):
  1. max_concurrent_positions     - cap total simultaneous open positions.
  2. max_daily_loss_usd           - halt new entries after the day's realized loss.
  3. max_same_direction_positions - correlation cap (scaffold; off by default).

This is pure decision logic: the caller supplies the current open positions and
the day's realized PnL, so the guard stays broker-agnostic and unit-testable.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

logger = logging.getLogger(__name__)


def _pair_ccys(symbol: Optional[str]) -> Optional[tuple[str, str]]:
    """(base, quote) for a standard 6-char FX symbol, else None.
    EURUSD -> (EUR, USD); USDJPY -> (USD, JPY); XAUUSD -> (XAU, USD)."""
    if not symbol or len(symbol) < 6:
        return None
    s = symbol.upper()
    return s[:3], s[3:6]


def _ccy_exposure(symbol: Optional[str], direction: Optional[str]) -> dict:
    """Signed per-currency units for one position (count-based, 1 unit/position).
    BUY a pair = long base / short quote; SELL = the reverse. All 4 traded pairs
    share USD, so this is what makes e.g. USDJPY BUY and AUDUSD SELL both read as
    +USD -- the correlated 'long USD' bet that looks like two opposite trades."""
    cc = _pair_ccys(symbol)
    if not cc or direction not in ("BUY", "SELL"):
        return {}
    base, quote = cc
    sign = 1.0 if direction == "BUY" else -1.0
    return {base: sign, quote: -sign}


def _position_symbol(pos) -> Optional[str]:
    return pos.get("symbol") if isinstance(pos, dict) else getattr(pos, "symbol", None)


def _position_direction(pos) -> Optional[str]:
    """Best-effort BUY/SELL from a bridge position dict or an MT5 position obj.

    MT5: POSITION_TYPE_BUY = 0, POSITION_TYPE_SELL = 1.
    """
    t = pos.get("type") if isinstance(pos, dict) else getattr(pos, "type", None)
    if t in (0, "BUY", "Buy", "buy"):
        return "BUY"
    if t in (1, "SELL", "Sell", "sell"):
        return "SELL"
    return None


class PortfolioGuard:
    """Account-wide pre-trade checks. Returns (allowed, reason) from `check`."""

    def __init__(self, config: dict):
        self.max_concurrent = int(config.get("max_concurrent_positions", 5) or 0)
        self.max_daily_loss_usd = float(config.get("max_daily_loss_usd", 500.0) or 0.0)
        self.max_same_direction = int(config.get("max_same_direction_positions", 0) or 0)
        self.max_currency_exposure = float(config.get("max_currency_exposure", 0) or 0.0)

    def check(
        self,
        open_positions: Sequence,
        realized_pnl_today: float,
        intended_direction: Optional[str] = None,
        intended_symbol: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Evaluate all enabled caps. `reason` is '' when allowed."""
        n = len(open_positions)

        # 1. Concurrent-position cap (account-wide, across all symbols/magics).
        if self.max_concurrent > 0 and n >= self.max_concurrent:
            return False, f"portfolio_max_concurrent ({n} >= {self.max_concurrent})"

        # 2. Daily realized-loss halt. Equity-independent, so it works in bridge
        #    mode where RiskGuard's equity drawdown never seeds.
        if self.max_daily_loss_usd > 0 and realized_pnl_today <= -self.max_daily_loss_usd:
            return False, (
                f"portfolio_daily_loss_halt "
                f"(realized {realized_pnl_today:.2f} <= -{self.max_daily_loss_usd:.2f})"
            )

        # 3. Correlation cap: max same-direction concurrent positions. Scaffold,
        #    disabled when max_same_direction == 0 (aggressive default).
        if self.max_same_direction > 0 and intended_direction:
            same = sum(1 for p in open_positions if _position_direction(p) == intended_direction)
            if same >= self.max_same_direction:
                return False, (
                    f"portfolio_max_same_direction "
                    f"({same} {intended_direction} >= {self.max_same_direction})"
                )

        # 4. Currency-exposure cap: net signed units per currency across all open
        #    positions PLUS the intended one. Because every traded pair carries a
        #    USD leg, this catches correlated bets that look independent -- e.g.
        #    USDJPY BUY (+USD) and AUDUSD SELL (+USD) net to +2 USD, one doubled
        #    'long USD' position that stops out together. Cap 1 permits offsetting
        #    (hedged) pairs but blocks stacking the same currency direction.
        if self.max_currency_exposure > 0 and intended_direction and intended_symbol:
            net: dict = {}
            for p in open_positions:
                for c, u in _ccy_exposure(_position_symbol(p), _position_direction(p)).items():
                    net[c] = net.get(c, 0.0) + u
            for c, u in _ccy_exposure(intended_symbol, intended_direction).items():
                net[c] = net.get(c, 0.0) + u
            for c, u in net.items():
                if abs(u) > self.max_currency_exposure:
                    return False, (
                        f"portfolio_currency_exposure "
                        f"({c} net {u:+.1f} > {self.max_currency_exposure:.1f})"
                    )

        return True, ""


__all__ = ["PortfolioGuard"]
