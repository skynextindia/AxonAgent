"""Trade Analytics Tracker.

Captures the COMPLETE engine decision context (entry signature + management +
exit criteria) per trade, so every trade is fully diagnosable off-line:
"How do high-vel_pct EXHAUSTION entries at-structure perform in TREND regimes?"
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime

from axonai.realtime.reversal_model import EngineSnapshot


def _pip(symbol: str) -> float:
    s = (symbol or "").upper()
    return 0.01 if ("JPY" in s or "XAU" in s) else 0.0001


def _g(obj, name, default=0.0):
    """Defensive getattr → coerce to the default's type."""
    v = getattr(obj, name, default) if obj is not None else default
    if v is None:
        return default
    return v


def _classify_gate(reason: str) -> str:
    r = (reason or "").lower()
    if "thesis" in r: return "thesis_failure"
    if "adverse" in r: return "adverse_impulse"
    if "exhaustion" in r: return "exhaustion"
    if "trail" in r: return "trailing"
    if "take profit" in r or "tp" in r: return "take_profit"
    if "stop loss" in r or "sl hit" in r or "hard" in r: return "hard_sl"
    if "eod" in r or "session" in r: return "eod"
    if "manual" in r: return "manual"
    return "other"


@dataclass
class TradeRecord:
    """Complete lifecycle record of a single trade (full engine context)."""
    ticket: int
    symbol: str
    direction: str
    entry_time: str
    entry_price: float
    initial_sl: float
    initial_tp: float

    # ── Entry context: labels ──────────────────────────────────────────────
    regime: str
    regime_confidence: float
    mtf_alignment: float
    mtf_context: str
    anomaly_velocity_z: float
    displacement_classification: str
    nearest_support: float
    nearest_resistance: float

    # ── Entry context: the reversal SIGNATURE (velocity) ───────────────────
    vel_pct: float = 0.0
    vel_tick_eff: float = 0.0
    vel_vol_pips: float = 0.0
    vel_decay_ratio: float = 0.0
    vel_is_unusual: bool = False
    displacement_ratio: float = 0.0
    net_displacement_pips: float = 0.0
    # MTF detail + reversal pressure
    mtf_h4_bias: float = 0.0
    mtf_h1_bias: float = 0.0
    mtf_m15_bias: float = 0.0
    reversal_pressure: float = 0.0
    volatility: str = ""
    # Location (the biggest edge)
    at_structure: bool = False
    distance_to_sr: float = 0.0
    room_available: float = 0.0
    nearest_level_type: str = ""
    # Liquidity (sweep confirmation)
    active_sweeps: int = 0
    active_breaks: int = 0
    liquidity_void: bool = False
    # Order context
    signal_quality: float = 0.0
    confluence_score: float = 0.0    # raw unified-gate score at entry (reversal_model ~line 530); signal_quality over-reports
    entry_style: str = ""
    initial_sl_pips: float = 0.0

    # ── Execution quality (fill telemetry) ─────────────────────────────────
    entry_requested_price: float = 0.0      # signal price the order was sent at
    entry_fill_price: float = 0.0           # actual broker fill price
    entry_spread_pips: float = 0.0          # spread paid at entry
    entry_slippage_pips: float = 0.0        # signed adverse pips: + = filled worse than signal
    profit_protect_pips_ref: float = 0.0    # exit floor ref = 4.0*clamp(vol_pips,0.5,3.0)

    # ── Exit: outcome + criteria + engine state at the cut ─────────────────
    exit_time: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    exit_gate: str = ""
    pips_profit: float = 0.0
    r_multiple: float = 0.0
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    time_in_drawdown_sec: float = 0.0
    health_score_at_exit: float = 0.0
    ticks_in_trade: int = 0
    exit_vel_pct: float = 0.0
    exit_displacement: str = ""
    exit_phase: str = ""
    exit_thesis: str = ""
    # Who closed it: "engine" (a gate fired) or "broker" (TP/SL/stop-out hit).
    # Broker closes have no gate; without this they were absent from the log
    # entirely, which silently censored every TP winner out of the sample.
    exit_source: str = "engine"
    # Realized money. pips_profit alone is not comparable across trades once
    # position size varies, and it is not comparable at all across symbols.
    volume: float = 0.0
    profit_usd: float = 0.0
    # Version of the entry logic + active experiment flags that produced this
    # trade (see default_config.strategy_version). Lets later analysis attribute
    # each fill to a configuration directly instead of inferring it.
    strategy_version: str = ""
    # Why the trade was accepted: state-machine state at fire (ARMING / RETEST_WAIT
    # / TRIGGERED) and the setup source that armed it. Signal-level importance
    # ranked state/setup above every continuous feature, so these are the fields to
    # group by when asking "which setup/state is actually profitable".
    entry_state: str = ""
    setup_source: str = ""


class TradeAnalytics:
    """Records full-context trade history for off-line evaluation."""

    def __init__(self, log_dir: str = "reports"):
        self._log_dir = log_dir
        os.makedirs(self._log_dir, exist_ok=True)
        self._log_file = os.path.join(self._log_dir, "trade_analytics.jsonl")
        self._active_trades: dict[int, TradeRecord] = {}

    def record_entry(
        self,
        ticket: int,
        symbol: str,
        direction: str,
        entry_price: float,
        sl: float,
        tp: float,
        snapshot: EngineSnapshot,
        entry_style: str = "",
        spread_pips: float | None = None,
        fill_price: float | None = None,
        strategy_version: str = "",
    ) -> None:
        """Create a trade record capturing the full entry decision.

        entry_price is the requested/signal price. Pass fill_price (actual broker
        fill) and spread_pips to capture execution quality; both are optional so
        existing callers keep working unchanged.
        """
        liq = getattr(snapshot, "liquidity", None)
        v = getattr(snapshot, "velocity", None)
        d = getattr(snapshot, "displacement", None)
        mtf = getattr(snapshot, "mtf", None)
        rg = getattr(snapshot, "regime", None)
        lc = getattr(snapshot, "location_context", None)
        ed = getattr(snapshot, "entry_decision", None)

        sup = getattr(liq, "nearest_support", None)
        res = getattr(liq, "nearest_resistance", None)
        pip = _pip(symbol)

        record = TradeRecord(
            ticket=ticket, symbol=symbol, direction=direction,
            entry_time=datetime.now().isoformat(),
            entry_price=entry_price, initial_sl=sl, initial_tp=tp,
            regime=str(_g(rg, "regime", "")),
            regime_confidence=round(float(_g(rg, "confidence", 0.0)), 3),
            mtf_alignment=round(float(_g(mtf, "alignment_score", 0.0)), 3),
            mtf_context=str(_g(mtf, "context_summary", "")),
            anomaly_velocity_z=round(float(_g(v, "z_score", 0.0)), 2),
            displacement_classification=str(_g(d, "classification", "")),
            nearest_support=float(_g(sup, "price", 0.0)),
            nearest_resistance=float(_g(res, "price", 0.0)),
        )
        record.strategy_version = strategy_version
        record.entry_state = str(_g(ed, "state", "") or "")
        record.setup_source = str(getattr(snapshot, "candle_setup_source", "") or "")
        # Raw confluence score at the gate (not signal_quality, which is inflated by
        # the state-machine max). This is the number to correlate against pips_profit.
        record.confluence_score = round(float(_g(ed, "confluence_score", 0.0) or 0.0), 3)
        # Reversal signature (velocity) — the field set our analysis proved matters
        record.vel_pct = round(float(_g(v, "percentile", 0.0)), 2)
        record.vel_tick_eff = round(float(_g(v, "tick_efficiency", 0.0)), 3)
        record.vel_vol_pips = round(float(_g(v, "vol_pips", 0.0)), 3)
        record.vel_decay_ratio = round(float(_g(v, "decay_ratio", 0.0)), 3)
        record.vel_is_unusual = bool(_g(v, "is_unusual", False))
        record.displacement_ratio = round(float(_g(d, "displacement_ratio", 0.0)), 3)
        record.net_displacement_pips = round(float(_g(d, "net_displacement_pips", 0.0)), 2)
        record.mtf_h4_bias = round(float(_g(mtf, "h4_bias", 0.0)), 2)
        record.mtf_h1_bias = round(float(_g(mtf, "h1_bias", 0.0)), 2)
        record.mtf_m15_bias = round(float(_g(mtf, "m15_bias", 0.0)), 2)
        record.reversal_pressure = round(float(_g(mtf, "reversal_pressure", 0.0)), 3)
        record.volatility = str(_g(rg, "volatility", ""))
        # Location
        record.at_structure = bool(_g(lc, "at_structure", False))
        record.distance_to_sr = round(float(_g(lc, "distance_to_sr", 0.0)), 3)
        record.room_available = round(float(_g(lc, "room_available", 0.0)), 2)
        record.nearest_level_type = str(_g(lc, "nearest_level_type", ""))
        # Liquidity
        record.active_sweeps = len(_g(liq, "active_sweeps", []) or [])
        record.active_breaks = len(_g(liq, "active_breaks", []) or [])
        record.liquidity_void = bool(_g(liq, "liquidity_void_active", False))
        # Order context
        record.signal_quality = round(float(_g(ed, "signal_quality", 0.0)), 3)
        record.entry_style = entry_style
        record.initial_sl_pips = round(abs(entry_price - sl) / pip, 1) if pip else 0.0

        # Execution quality: requested vs fill, spread, and the exit protection floor
        record.entry_requested_price = round(float(entry_price), 6)
        if spread_pips is not None:
            record.entry_spread_pips = round(float(spread_pips), 2)
        if fill_price is not None:
            fp = float(fill_price)
            record.entry_fill_price = round(fp, 6)
            # signed adverse slippage: positive = filled worse than the signal price
            slip = (fp - entry_price) if str(direction).upper().startswith("B") else (entry_price - fp)
            record.entry_slippage_pips = round(slip / pip, 2) if pip else 0.0
        # Exit floor reference — mirrors exit_engine profit_protect_pips (4.0 * vol_scale).
        # Lets the exit diagnosis compare the cut-gate floor against each trade's MFE offline.
        vp = record.vel_vol_pips
        vol_scale = max(0.5, min(vp / 1.0, 3.0))
        record.profit_protect_pips_ref = round(4.0 * vol_scale, 2)

        self._active_trades[ticket] = record

    def record_exit(
        self,
        ticket: int,
        exit_price: float,
        pips_profit: float,
        exit_reason: str,
        snapshot: EngineSnapshot,
        exit_time: str | None = None,
        profit_usd: float | None = None,
        volume: float | None = None,
        exit_source: str = "engine",
    ) -> None:
        """Complete the record with exit criteria + engine state at the cut.

        exit_time/profit_usd/volume/exit_source are optional so existing callers
        keep working. Pass them from the broker-close detector so TP/SL exits land
        in the log with the real deal time and realized money, not just pips.
        """
        if ticket not in self._active_trades:
            # Already completed (the engine-close path records and deletes before
            # the position-closed detector sees the ticket vanish), or never opened
            # by us (adopted / manual trade). Either way there is nothing to finish.
            return
        record = self._active_trades[ticket]
        th = getattr(snapshot, "trade_health", None)
        ts = getattr(snapshot, "trade_state", None)
        v = getattr(snapshot, "velocity", None)
        d = getattr(snapshot, "displacement", None)

        record.exit_time = exit_time or datetime.now().isoformat()
        record.exit_price = exit_price
        record.pips_profit = round(float(pips_profit), 2)
        record.exit_reason = exit_reason
        record.exit_gate = _classify_gate(exit_reason)
        record.exit_source = exit_source
        if profit_usd is not None:
            record.profit_usd = round(float(profit_usd), 2)
        if volume is not None:
            record.volume = round(float(volume), 2)
        record.r_multiple = round(pips_profit / record.initial_sl_pips, 2) if record.initial_sl_pips else 0.0
        record.max_favorable_excursion = round(float(_g(th, "max_favorable_excursion", 0.0)), 1)
        record.max_adverse_excursion = round(float(
            _g(th, "max_adverse_excursion", None) if getattr(th, "max_adverse_excursion", None) is not None
            else _g(ts, "mae", 0.0)), 1)
        record.time_in_drawdown_sec = round(float(_g(th, "time_in_drawdown_sec", 0.0)), 1)
        record.health_score_at_exit = round(float(_g(th, "score", 0.0)), 2)
        record.ticks_in_trade = int(_g(ts, "ticks_in_trade", 0))
        # Engine state at the moment the gate fired
        record.exit_vel_pct = round(float(_g(v, "percentile", 0.0)), 2)
        record.exit_displacement = str(_g(d, "classification", ""))
        record.exit_phase = str(_g(ts, "current_phase", ""))
        record.exit_thesis = str(_g(ts, "thesis_status", ""))

        self._write_record(record)
        del self._active_trades[ticket]

    def _write_record(self, record: TradeRecord) -> None:
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(record)) + "\n")
        except Exception:
            pass  # never let logging I/O interrupt the daemon


__all__ = ["TradeAnalytics", "TradeRecord"]
