"""State objects for the isolated Risk Engine prototype.

Pure data + tiny helpers. No I/O, no MT5, no production imports.

Design rule (from the phase brief): **do not assume missing values.** Any input
the caller cannot obtain safely must be passed as ``None`` (== UNAVAILABLE). The
engine treats UNAVAILABLE explicitly — it degrades or refuses, it never invents.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any

# Sentinel: a value that could not be obtained safely. Kept distinct from 0.0,
# which is a legitimate number (e.g. zero open risk).
UNAVAILABLE = None


# ── prop-firm profile ───────────────────────────────────────────────────────
# Mirrors the LIVE node's launch flags so simulation is faithful. These are the
# account's real limits, NOT recommended risk parameters.
#   run_system.bat node line: --prop-initial-balance 100000
#                             --prop-max-drawdown-pct 6.0 --prop-daily-loss-pct 3.0
#   risk_guard.py:35-37  safety_factor = 1 - buffer/100  (buffer default 20 → 0.80)
#   risk_guard.py:207-211 static buffered floor = initial*(1 - dd%*safety_factor)
#   risk_guard.py:213-223 firm floor          = initial*(1 - dd%)
#   risk_guard.py:287-289 buffered daily limit = daily%*safety_factor
@dataclass(frozen=True)
class PropProfile:
    """Prop-account limits (faithful copy of the node's config; not a policy)."""
    initial_balance: float = 100_000.0
    max_drawdown_pct: float = 6.0          # overall floor from initial
    daily_loss_pct: float = 3.0            # daily loss limit (server day)
    safety_buffer_pct: float = 20.0        # trip buffer -> safety_factor
    trailing: bool = False                 # node runs static (prop_max_drawdown_trailing False)

    @property
    def safety_factor(self) -> float:
        return max(0.0, min(1.0, 1.0 - self.safety_buffer_pct / 100.0))

    def buffered_floor(self, peak_equity: Optional[float] = None) -> float:
        """Buffered drawdown floor (the tripwire the guard actually uses)."""
        eff = self.max_drawdown_pct * self.safety_factor
        base = max(peak_equity or 0.0, self.initial_balance) if self.trailing else self.initial_balance
        return base * (1.0 - eff / 100.0)

    def firm_floor(self, peak_equity: Optional[float] = None) -> float:
        """The firm's real breach line (no buffer)."""
        base = max(peak_equity or 0.0, self.initial_balance) if self.trailing else self.initial_balance
        return base * (1.0 - self.max_drawdown_pct / 100.0)

    def buffered_daily_limit_pct(self) -> float:
        return self.daily_loss_pct * self.safety_factor


# Named default = the live node profile. Buffered floor 95,200 / firm 94,000 /
# buffered daily 2.4%. Provided for simulation faithfulness only.
NODE_PROFILE = PropProfile()


@dataclass
class OpenPosition:
    """A currently-open position, as the engine sees it (no MT5 handle)."""
    symbol: str
    direction: str                 # "BUY" / "SELL"
    lot: float
    entry_price: float
    stop_price: Optional[float] = None
    open_risk_usd: Optional[float] = None   # stop-loss $-risk if known


@dataclass
class RiskState:
    """INPUT to the Risk Engine. One prospective entry + account context.

    Every field the caller cannot obtain safely stays ``None`` (UNAVAILABLE);
    the engine reports it as a warning rather than guessing.
    """
    # account
    equity: Optional[float] = UNAVAILABLE
    balance: Optional[float] = UNAVAILABLE
    initial_balance: Optional[float] = UNAVAILABLE
    # candidate trade
    symbol: str = ""
    direction: str = ""                     # "BUY"/"SELL"
    entry_price: Optional[float] = UNAVAILABLE
    stop_price: Optional[float] = UNAVAILABLE
    stop_distance_pips: Optional[float] = UNAVAILABLE
    atr: Optional[float] = UNAVAILABLE
    # risk-state context
    current_daily_loss_pct: Optional[float] = UNAVAILABLE     # >=0 means a loss
    current_drawdown_pct: Optional[float] = UNAVAILABLE       # from initial, >=0 loss
    distance_to_buffered_floor: Optional[float] = UNAVAILABLE # $ above buffered floor
    distance_to_firm_floor: Optional[float] = UNAVAILABLE     # $ above firm floor
    # exposure
    existing_positions: List[OpenPosition] = field(default_factory=list)
    existing_open_risk_usd: Optional[float] = UNAVAILABLE
    correlated_open_risk_usd: Optional[float] = UNAVAILABLE
    # instrument
    account_currency: str = "USD"
    pip_value: Optional[float] = UNAVAILABLE     # $/pip/lot
    min_lot: float = 0.1                         # default_config.py:185
    max_lot: float = 2.0                         # SYMBOL_CALIBRATION max_lot
    lot_step: float = 0.01

    def missing(self) -> List[str]:
        """Names of inputs that are UNAVAILABLE (for warnings/telemetry)."""
        out = []
        for k in ("equity", "entry_price", "stop_distance_pips", "pip_value"):
            if getattr(self, k) is None:
                out.append(k)
        return out


@dataclass
class RiskDecision:
    """OUTPUT of the Risk Engine. Structured, deterministic, no side effects."""
    allowed: bool = False
    risk_pct: Optional[float] = None
    risk_usd: Optional[float] = None
    lot_size: Optional[float] = None
    base_risk: Optional[float] = None            # base_risk_pct before scaling
    floor_scale: float = 1.0
    correlation_scale: float = 1.0
    daily_loss_scale: float = 1.0
    final_scale: float = 1.0
    projected_risk_pct: Optional[float] = None   # risk_usd / equity
    projected_floor_distance: Optional[float] = None  # $ above buffered floor if this trade full-stops
    correlated_exposure: Optional[float] = None  # net USD notional bucket incl. candidate
    decision_reason: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
