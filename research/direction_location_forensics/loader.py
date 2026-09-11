"""Read-only loader: reconstruct historical trades from the production journals.

Joins the rich ENTRY-signal rows (direction, peak_type, velocity, mtf bias, S/R
state, regime, shadow fields) to the TRADE_CLOSED rows (entry/exit/pips/reason/
outcome, and MFE/MAE/hold on the subset that has them) via
``entry.trade_result.order == trade_closed.ticket``.

Nothing is written back. Every field that does not exist in the source is left
as ``None`` == UNAVAILABLE — never invented.
"""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

# Repo-root-relative journals (read-only). Resolved from this file so the CWD
# cannot redirect them.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
LEAD_JOURNAL = os.path.join(_REPO_ROOT, "reports", "signals.jsonl")
NODE_JOURNAL = os.path.join(_REPO_ROOT, "reports", "signals_node.jsonl")

UNAVAILABLE = None


def _pip_size(symbol: str) -> float:
    s = (symbol or "").upper()
    return 0.01 if ("JPY" in s or "XAU" in s) else 0.0001


def _norm_symbol(symbol: str) -> str:
    s = (symbol or "").upper().replace("=X", "")
    if s.endswith(".I"):
        s = s[:-2]
    return s


def _to_dir(x: Optional[str]) -> Optional[str]:
    if not x:
        return None
    x = str(x).upper()
    if x in ("BUY", "BULLISH", "OVERWEIGHT"):
        return "BUY"
    if x in ("SELL", "BEARISH", "UNDERWEIGHT"):
        return "SELL"
    if x in ("BUY", "SELL"):
        return x
    return None


def _read_jsonl(path: str) -> List[dict]:
    """Read a JSONL file read-only. Missing file -> empty list (no error)."""
    rows: List[dict] = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except Exception:
                continue
    return rows


def _get(d: Any, *keys, default=None):
    """Nested dict get: _get(row, 'event_details', 'mtf_alignment')."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
    return cur


@dataclass
class ForensicTrade:
    """One reconstructed trade with everything we could source (else UNAVAILABLE)."""
    # identity / source
    account: str = ""                    # "lead" | "node"
    ticket: Optional[int] = None
    system: Optional[str] = None
    timestamp: Optional[str] = None
    timestamp_utc: Optional[str] = None
    symbol: Optional[str] = None
    # direction + prices
    direction: Optional[str] = None      # BUY / SELL
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    stop_price: Optional[float] = None
    stop_pips: Optional[float] = None
    # realized outcome
    volume: Optional[float] = None
    profit: Optional[float] = None
    pips: Optional[float] = None
    outcome: Optional[str] = None        # WIN / LOSS
    exit_reason: Optional[str] = None
    # excursions / timing (subset)
    mfe_pips: Optional[float] = None
    mae_pips: Optional[float] = None
    hold_seconds: Optional[float] = None
    # entry context (lead only; node UNAVAILABLE)
    peak_type: Optional[str] = None
    event_direction: Optional[str] = None
    velocity_divergence: Optional[float] = None
    tick_efficiency: Optional[float] = None
    peak_confidence: Optional[float] = None
    mtf_alignment: Optional[str] = None
    daily_trend: Optional[str] = None
    displacement_ratio: Optional[float] = None   # impulse_shadow
    dominant_regime: Optional[str] = None
    regime_confidence: Optional[float] = None
    volatility: Optional[str] = None
    spread_pips: Optional[float] = None
    sr_level_type: Optional[str] = None
    sr_level_price: Optional[float] = None
    sr_level_dist_pips: Optional[float] = None
    structure_state: Optional[str] = None        # with/against structure (structure_shadow)
    # explicitly-unavailable in history (documented for the reader)
    atr: Optional[float] = None
    liquidity_state: Optional[str] = None
    room_score: Optional[float] = None
    equity_before: Optional[float] = None
    distance_to_prop_floor: Optional[float] = None
    correlated_exposure: Optional[float] = None
    # bookkeeping
    joined_context: bool = False
    missing: List[str] = field(default_factory=list)

    def __post_init__(self):
        # Self-populate the UNAVAILABLE set so a directly-constructed trade reports
        # it too; build_trades() recomputes this after joining the entry context.
        if not self.missing:
            self.missing = [k for k in _TRACKED_MISSING if getattr(self, k) is None]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Fields the reader should know are UNAVAILABLE for a given trade (load-bearing set)
_TRACKED_MISSING = (
    "stop_pips", "mfe_pips", "mae_pips", "hold_seconds", "atr", "mtf_alignment",
    "velocity_divergence", "displacement_ratio", "sr_level_type", "structure_state",
    "liquidity_state", "room_score", "equity_before", "distance_to_prop_floor",
    "correlated_exposure",
)


def _index_entries(rows: List[dict]) -> Dict[int, dict]:
    """Map order-ticket -> entry-signal row (the rich context)."""
    idx: Dict[int, dict] = {}
    for r in rows:
        if r.get("type") in ("trade_closed", "signal_skipped"):
            continue
        order = _get(r, "trade_result", "order")
        if order:
            idx[int(order)] = r
    return idx


def _structure_state(entry: dict) -> Optional[str]:
    ss = entry.get("structure_shadow")
    if isinstance(ss, dict):
        for k in ("state", "structure", "verdict", "label"):
            if ss.get(k):
                return str(ss[k])
    return None


def _displacement(entry: dict) -> Optional[float]:
    imp = entry.get("impulse_shadow")
    if isinstance(imp, dict):
        for k in ("displacement_ratio", "displacement", "ratio"):
            v = imp.get(k)
            if isinstance(v, (int, float)):
                return float(v)
    return None


def build_trades(journal_rows: List[dict], account: str) -> List[ForensicTrade]:
    """Turn one journal's rows into joined ForensicTrade records."""
    entries = _index_entries(journal_rows)
    trades: List[ForensicTrade] = []
    for r in journal_rows:
        if r.get("type") != "trade_closed":
            continue
        sym = _norm_symbol(r.get("symbol"))
        pip = _pip_size(sym)
        t = ForensicTrade(
            account=account,
            ticket=r.get("ticket"),
            system=r.get("system"),
            timestamp=r.get("timestamp"),
            timestamp_utc=r.get("timestamp_utc"),
            symbol=sym,
            direction=_to_dir(r.get("direction")),
            entry_price=r.get("entry_price"),
            exit_price=r.get("exit_price"),
            volume=r.get("volume"),
            profit=r.get("profit"),
            pips=r.get("pips"),
            outcome=r.get("outcome"),
            exit_reason=r.get("reason"),
            mfe_pips=r.get("mfe_pips"),
            mae_pips=r.get("mae_pips"),
            hold_seconds=r.get("hold_seconds"),
        )
        # join the rich entry context by ticket
        entry = entries.get(int(r["ticket"])) if r.get("ticket") else None
        if entry:
            t.joined_context = True
            sl = _get(entry, "trade_result", "sl")
            entry_px = _get(entry, "trade_result", "price") or t.entry_price
            if sl and entry_px:
                t.stop_price = float(sl)
                sp = abs(float(entry_px) - float(sl)) / pip
                t.stop_pips = sp if sp > 0 else None
            t.peak_type = _get(entry, "event_details", "peak_type")
            t.event_direction = _get(entry, "event_details", "direction")
            t.velocity_divergence = _get(entry, "event_details", "velocity_divergence")
            t.tick_efficiency = _get(entry, "event_details", "price_per_tick_efficiency")
            t.peak_confidence = _get(entry, "event_details", "peak_confidence")
            t.mtf_alignment = _get(entry, "event_details", "mtf_alignment")
            t.daily_trend = entry.get("daily_trend")
            t.displacement_ratio = _displacement(entry)
            t.dominant_regime = entry.get("dominant_regime")
            t.regime_confidence = entry.get("regime_confidence")
            t.volatility = entry.get("volatility")
            t.spread_pips = entry.get("spread_pips")
            t.sr_level_type = entry.get("sr_level_type")
            t.sr_level_price = entry.get("sr_level_price")
            t.sr_level_dist_pips = entry.get("sr_level_dist_pips")
            t.structure_state = _structure_state(entry)
        # record what is UNAVAILABLE for this trade
        t.missing = [k for k in _TRACKED_MISSING if getattr(t, k) is None]
        trades.append(t)
    return trades


def load_all() -> Dict[str, List[ForensicTrade]]:
    """Load lead + node histories. Read-only."""
    lead = build_trades(_read_jsonl(LEAD_JOURNAL), "lead")
    node = build_trades(_read_jsonl(NODE_JOURNAL), "node")
    return {"lead": lead, "node": node}


def parse_ts(t: ForensicTrade) -> Optional[datetime]:
    for s in (t.timestamp_utc, t.timestamp):
        if not s:
            continue
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                return datetime.strptime(s.split(".")[0].replace("Z", ""), fmt)
            except Exception:
                continue
    return None
