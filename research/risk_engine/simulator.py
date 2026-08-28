"""Historical risk-policy simulator (READ-ONLY replay).

Replays the FULL available trade journal and computes HYPOTHETICAL results under
configurable risk policies, by re-sizing each trade and holding the realized price
path (``pips``) fixed. It isolates the SIZING effect; it does not re-simulate
RiskGuard flatten TIMING (see LIMITATIONS).

Safety:
  * Reads ``reports/signals*.jsonl`` READ-ONLY. Never writes them.
  * Writes only under ``research/risk_engine/shadow_out/``.
  * Imports nothing from ``axonai``; no MT5; no execution.

LIMITATIONS (state them, do not hide them):
  1. Close-level data -> equity/floor are evaluated at each EXIT (realized), so
     intra-trade floating dips are invisible. True drawdown is deeper.
  2. The realized ``pips`` already embed the ACTUAL exits (incl. the 08-11
     RiskGuard flatten). Re-sizing scales P&L on those SAME exits; it does NOT
     recompute whether a smaller size would have avoided that day's daily-loss
     flatten. So 'forced_flatten_equivalents' is a close-granularity proxy.
  3. Equity-at-entry uses realized-only equity (no floating), because floating
     MAE is not in the journal. Production sizes off live equity incl. floating.
  4. Stop distance is reconstructed (hard-distance 20/30 pips); the journal has
     no per-trade stop field. Sensitivity to this is a first-class caveat.
"""

from __future__ import annotations

import os
import json
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

from .models import RiskState, OpenPosition, PropProfile, NODE_PROFILE
from .risk_engine import RiskPolicy, decide
from .correlation import signed_usd_notional
from .telemetry import ShadowTelemetryWriter, SHADOW_OUT_DIR

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NODE_JOURNAL = os.path.join(_REPO_ROOT, "reports", "signals_node.jsonl")
LEAD_JOURNAL = os.path.join(_REPO_ROOT, "reports", "signals.jsonl")

# Reconstructed hard-distance stops (run_system.bat --hard-distance; SYMBOL_CALIBRATION
# hard_stop_pips EURUSD 20 / USDJPY 30). Configurable; sensitivity is a caveat.
DEFAULT_STOP_PIPS = {"EURUSD": 20.0, "USDJPY": 30.0}


# ── trade record ────────────────────────────────────────────────────────────
@dataclass
class Trade:
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    volume: float          # ACTUAL executed lot (journal)
    pips: float            # realized, signed (loss negative)
    profit: float          # ACTUAL realized $ (journal)
    reason: str
    outcome: str
    close_ts: float        # epoch seconds
    entry_ts: float        # epoch seconds (close - hold_seconds)
    pip_value: float       # $/pip/lot, empirical from the journal row
    stop_pips: float

    # filled during replay
    hypo_lot: Optional[float] = None
    hypo_risk_usd: Optional[float] = None
    hypo_pnl: Optional[float] = None
    allowed: bool = True
    reduced: bool = False


def _parse_ts(row: Dict[str, Any]) -> Optional[float]:
    s = row.get("timestamp_utc") or row.get("timestamp")
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    return None


def _canon(symbol: str) -> str:
    letters = "".join(c for c in (symbol or "").upper() if c.isalpha())
    return letters[:6] if len(letters) >= 6 else letters


def _empirical_pip_value(profit: float, pips: float, volume: float, symbol: str,
                         price: float) -> float:
    """$/pip/lot from the row itself when possible; else the production fallback."""
    if pips and volume and abs(pips) > 1e-9 and volume > 1e-9:
        pv = abs(profit) / (abs(pips) * volume)
        if pv > 0.01:
            return pv
    # fallback mirrors trade_executor._pip_value_per_lot
    c = _canon(symbol)
    if c.endswith("USD"):
        return 10.0
    if price and price > 0:
        return 100_000.0 * 0.01 / price
    return 6.3


def load_trades(journal_path: str,
                stop_pips_map: Dict[str, float] = None) -> List[Trade]:
    """Load + reconstruct trades from a journal (READ-ONLY)."""
    stop_pips_map = stop_pips_map or DEFAULT_STOP_PIPS
    out: List[Trade] = []
    if not os.path.exists(journal_path):
        return out
    with open(journal_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("type") != "trade_closed":
                continue
            close_ts = _parse_ts(r)
            if close_ts is None:
                continue
            hold = float(r.get("hold_seconds") or 0.0)
            sym = _canon(r.get("symbol", ""))
            entry_price = float(r.get("entry_price") or 0.0)
            volume = float(r.get("volume") or 0.0)
            pips = float(r.get("pips") or 0.0)
            profit = float(r.get("profit") or 0.0)
            pv = _empirical_pip_value(profit, pips, volume, sym, entry_price)
            out.append(Trade(
                symbol=sym,
                direction=str(r.get("direction", "")).upper(),
                entry_price=entry_price,
                exit_price=float(r.get("exit_price") or 0.0),
                volume=volume,
                pips=pips,
                profit=profit,
                reason=str(r.get("reason", "")),
                outcome=str(r.get("outcome", "")),
                close_ts=close_ts,
                entry_ts=close_ts - hold,
                pip_value=pv,
                stop_pips=float(stop_pips_map.get(sym, 20.0)),
            ))
    out.sort(key=lambda t: t.entry_ts)
    return out


# ── metrics ─────────────────────────────────────────────────────────────────
@dataclass
class Metrics:
    policy: str = ""
    n_trades: int = 0
    final_equity: float = 0.0
    start_equity: float = 0.0
    max_drawdown_usd: float = 0.0
    max_drawdown_pct: float = 0.0
    min_equity: float = 0.0
    buffered_floor_breaches: int = 0
    firm_floor_breaches: int = 0
    first_buffered_breach_ts: Optional[str] = None
    daily_loss_events: int = 0
    forced_flatten_equivalents: int = 0
    rejected_trades: int = 0
    reduced_size_trades: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    win_rate: float = 0.0
    winner_exposure_usd: float = 0.0     # total risk_usd deployed on winning trades
    avg_risk_pct: float = 0.0
    max_correlated_exposure_usd: float = 0.0   # max concurrent gross USD-bucket notional
    max_daily_loss_usd: float = 0.0
    max_daily_loss_pct: float = 0.0
    breakdown: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        return d


def _regime(ts: float) -> str:
    d = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    if d <= "2026-06-30":
        return "R1_Jun"
    if d <= "2026-07-20":
        return "R2_earlyJul"
    if d <= "2026-08-05":
        return "R3_lateJul_ramp"
    return "R4_Aug_bleed"


def simulate(trades: List[Trade], policy: RiskPolicy,
             profile: PropProfile = NODE_PROFILE,
             telemetry: Optional[ShadowTelemetryWriter] = None) -> Metrics:
    """Event-driven replay of one policy. Pure w.r.t. inputs; only optional
    telemetry side-effect writes to the isolated shadow_out dir."""
    m = Metrics(policy=policy.name)
    start_eq = profile.initial_balance
    m.start_equity = start_eq
    equity = start_eq
    peak = start_eq
    m.min_equity = start_eq

    # event stream: (ts, kind, trade)   kind: 0=entry, 1=exit  (entry before exit on ties)
    events: List[Tuple[float, int, Trade]] = []
    for t in trades:
        events.append((t.entry_ts, 0, t))
        events.append((t.close_ts, 1, t))
    events.sort(key=lambda e: (e[0], e[1]))

    open_positions: List[Trade] = []
    # daily tracking (server-day by close date of realized P&L)
    day_start_equity: Dict[str, float] = {}
    day_realized: Dict[str, float] = {}
    daily_breach_days = set()

    risk_pcts: List[float] = []
    wins = losses = 0

    def _day(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()

    # per-bucket breakdown accumulators
    bd = {"month": {}, "symbol": {}, "direction": {}, "regime": {}}

    def _accum(key_type: str, key: str, pnl: float, is_win: bool):
        b = bd[key_type].setdefault(key, {"n": 0, "pnl": 0.0, "wins": 0})
        b["n"] += 1
        b["pnl"] += pnl
        b["wins"] += 1 if is_win else 0

    for ts, kind, t in events:
        if kind == 0:  # ENTRY -> size it
            # current gross bucket exposure (for max correlated exposure metric)
            existing = [OpenPosition(symbol=o.symbol, direction=o.direction,
                                     lot=(o.hypo_lot or 0.0), entry_price=o.entry_price,
                                     open_risk_usd=(o.hypo_risk_usd or 0.0))
                        for o in open_positions]
            open_risk = sum((o.hypo_risk_usd or 0.0) for o in open_positions)
            buf_floor = profile.buffered_floor(peak)
            firm_floor = profile.firm_floor(peak)
            d = _day(t.entry_ts)
            ds = day_start_equity.get(d, equity)
            daily_loss_pct = max(0.0, (ds - equity) / ds * 100.0) if ds > 0 else 0.0

            st = RiskState(
                equity=equity, balance=equity, initial_balance=profile.initial_balance,
                symbol=t.symbol, direction=t.direction,
                entry_price=t.entry_price, stop_price=None,
                stop_distance_pips=t.stop_pips, atr=None,
                current_daily_loss_pct=daily_loss_pct,
                current_drawdown_pct=max(0.0, (profile.initial_balance - equity) /
                                         profile.initial_balance * 100.0),
                distance_to_buffered_floor=equity - buf_floor,
                distance_to_firm_floor=equity - firm_floor,
                existing_positions=existing,
                existing_open_risk_usd=open_risk,
                correlated_open_risk_usd=open_risk,
                pip_value=t.pip_value, min_lot=0.1, max_lot=12.0, lot_step=0.01,
            )
            dec = decide(st, policy, profile)
            t.allowed = dec.allowed
            t.hypo_lot = dec.lot_size or 0.0
            t.hypo_risk_usd = dec.risk_usd or 0.0
            t.reduced = dec.allowed and dec.final_scale < 1.0 - 1e-9
            if dec.allowed:
                risk_pcts.append(dec.risk_pct or 0.0)
                open_positions.append(t)
                # max concurrent gross USD-bucket notional (|signed USD notional|)
                gross = sum(abs(signed_usd_notional(o.symbol, o.direction,
                                                    (o.hypo_lot or 0.0), o.entry_price))
                            for o in open_positions)
                m.max_correlated_exposure_usd = max(m.max_correlated_exposure_usd, gross)
            else:
                m.rejected_trades += 1
            if telemetry is not None:
                telemetry.write_decision(
                    st, dec,
                    timestamp=datetime.fromtimestamp(t.entry_ts, tz=timezone.utc).isoformat(),
                    signal_id=f"{t.symbol}-{int(t.entry_ts)}")
            if t.reduced:
                m.reduced_size_trades += 1

        else:  # EXIT -> realize P&L
            if t not in open_positions:
                continue  # rejected at entry, nothing to realize
            open_positions.remove(t)
            pnl = t.pips * t.pip_value * (t.hypo_lot or 0.0)
            t.hypo_pnl = pnl
            equity += pnl
            m.n_trades += 1

            # daily
            d = _day(t.close_ts)
            if d not in day_start_equity:
                day_start_equity[d] = equity - pnl  # equity at day's first realized event
            day_realized[d] = day_realized.get(d, 0.0) + pnl

            # win/loss + gross
            is_win = pnl > 0
            if is_win:
                wins += 1
                m.gross_profit += pnl
                m.winner_exposure_usd += (t.hypo_risk_usd or 0.0)
            else:
                losses += 1
                m.gross_loss += -pnl

            # breakdowns
            month = datetime.fromtimestamp(t.close_ts, tz=timezone.utc).strftime("%Y-%m")
            _accum("month", month, pnl, is_win)
            _accum("symbol", t.symbol, pnl, is_win)
            _accum("direction", t.direction, pnl, is_win)
            _accum("regime", _regime(t.close_ts), pnl, is_win)

            # drawdown / floor at realized equity
            peak = max(peak, equity)
            m.min_equity = min(m.min_equity, equity)
            dd = peak - equity
            if dd > m.max_drawdown_usd:
                m.max_drawdown_usd = dd
                m.max_drawdown_pct = dd / peak * 100.0 if peak > 0 else 0.0
            buf_floor = profile.buffered_floor(peak)
            firm_floor = profile.firm_floor(peak)
            if equity <= buf_floor:
                m.buffered_floor_breaches += 1
                if m.first_buffered_breach_ts is None:
                    m.first_buffered_breach_ts = datetime.fromtimestamp(
                        t.close_ts, tz=timezone.utc).isoformat()
            if equity <= firm_floor:
                m.firm_floor_breaches += 1

            # daily-loss breach check (buffered daily limit)
            ds = day_start_equity[d]
            day_loss_pct = max(0.0, (ds - equity) / ds * 100.0) if ds > 0 else 0.0
            day_loss_usd = max(0.0, ds - equity)
            m.max_daily_loss_pct = max(m.max_daily_loss_pct, day_loss_pct)
            m.max_daily_loss_usd = max(m.max_daily_loss_usd, day_loss_usd)
            if day_loss_pct >= profile.buffered_daily_limit_pct() and d not in daily_breach_days:
                daily_breach_days.add(d)

    m.final_equity = equity
    m.daily_loss_events = len(daily_breach_days)
    m.forced_flatten_equivalents = len(daily_breach_days)  # proxy (see LIMITATIONS)
    total = wins + losses
    m.win_rate = wins / total * 100.0 if total else 0.0
    m.profit_factor = (m.gross_profit / m.gross_loss) if m.gross_loss > 1e-9 else float("inf")
    m.expectancy = (m.gross_profit - m.gross_loss) / total if total else 0.0
    m.avg_risk_pct = (sum(risk_pcts) / len(risk_pcts) * 100.0) if risk_pcts else 0.0
    m.breakdown = bd
    return m


# ── policy library (ILLUSTRATIVE — params external, NOT recommendations) ─────
def build_policies(base_pct: float = 0.011, fixed_usd: float = 1100.0) -> List[RiskPolicy]:
    """Return the A–F policy set.

    ``base_pct`` defaults to 0.011 = the CURRENT lead value (1.1%), used ONLY as a
    reference so the models are comparable. It is NOT a recommended parameter, and
    the drawdown/floor/correlation params below are illustrative bench settings,
    not validated thresholds. Nothing here is live.
    """
    return [
        # A: current node baseline
        RiskPolicy(name="A_fixed_1100usd", risk_mode="fixed_usd", fixed_usd=fixed_usd),
        # B: fixed percentage (current lead value, reference only)
        RiskPolicy(name="B_fixed_pct", risk_mode="pct", base_risk_pct=base_pct),
        # C: drawdown-scaled percentage (throttle as cushion shrinks)
        RiskPolicy(name="C_drawdown_scaled", risk_mode="pct", base_risk_pct=base_pct,
                   floor_mode="linear_taper", floor_taper_start_frac=0.06,
                   floor_taper_end_frac=0.01, floor_min_scale=0.25),
        # D: floor-aware percentage (keys off live distance-to-buffered-floor)
        RiskPolicy(name="D_floor_aware", risk_mode="pct", base_risk_pct=base_pct,
                   floor_mode="linear_taper", floor_taper_start_frac=0.04,
                   floor_taper_end_frac=0.005, floor_min_scale=0.1,
                   block_if_projected_breach=True),
        # E: correlation-aware sizing (shared USD unit)
        RiskPolicy(name="E_correlation_aware", risk_mode="pct", base_risk_pct=base_pct,
                   corr_mode="shared_unit", corr_shared_scale=0.5),
        # F: combined floor + correlation
        RiskPolicy(name="F_floor_plus_corr", risk_mode="pct", base_risk_pct=base_pct,
                   floor_mode="linear_taper", floor_taper_start_frac=0.04,
                   floor_taper_end_frac=0.005, floor_min_scale=0.1,
                   corr_mode="shared_unit", corr_shared_scale=0.5,
                   block_if_projected_breach=True),
    ]


def run_report(journal: str = NODE_JOURNAL, base_pct: float = 0.011,
               fixed_usd: float = 1100.0, write_telemetry: bool = False,
               profile: PropProfile = NODE_PROFILE) -> Dict[str, Any]:
    trades = load_trades(journal)
    policies = build_policies(base_pct=base_pct, fixed_usd=fixed_usd)
    results = {}
    for p in policies:
        tel = ShadowTelemetryWriter(filename=f"sim_{p.name}.jsonl") if write_telemetry else None
        m = simulate(trades, p, profile=profile, telemetry=tel)
        results[p.name] = m.to_dict()
    summary = {
        "journal": os.path.relpath(journal, _REPO_ROOT),
        "n_source_trades": len(trades),
        "profile": {"initial": profile.initial_balance,
                    "buffered_floor": profile.buffered_floor(),
                    "firm_floor": profile.firm_floor(),
                    "buffered_daily_pct": profile.buffered_daily_limit_pct()},
        "base_pct_reference": base_pct,
        "results": results,
    }
    return summary


def main():
    ap = argparse.ArgumentParser(description="Risk-policy historical simulator (READ-ONLY).")
    ap.add_argument("--journal", default=NODE_JOURNAL)
    ap.add_argument("--base-pct", type=float, default=0.011,
                    help="reference base risk fraction (default 0.011 = current lead 1.1%%)")
    ap.add_argument("--fixed-usd", type=float, default=1100.0)
    ap.add_argument("--telemetry", action="store_true", help="write shadow telemetry")
    ap.add_argument("--out", default=os.path.join(SHADOW_OUT_DIR, "sim_summary.json"))
    args = ap.parse_args()

    summary = run_report(journal=args.journal, base_pct=args.base_pct,
                         fixed_usd=args.fixed_usd, write_telemetry=args.telemetry)
    os.makedirs(SHADOW_OUT_DIR, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"source trades: {summary['n_source_trades']}  "
          f"buffered_floor={summary['profile']['buffered_floor']:.0f}")
    hdr = f"{'policy':<22}{'final_eq':>11}{'maxDD%':>8}{'bufBrch':>8}{'firmBrch':>9}{'PF':>7}{'avgRisk%':>9}"
    print(hdr)
    for name, r in summary["results"].items():
        pf = r["profit_factor"]
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"{name:<22}{r['final_equity']:>11.0f}{r['max_drawdown_pct']:>8.2f}"
              f"{r['buffered_floor_breaches']:>8}{r['firm_floor_breaches']:>9}"
              f"{pf_s:>7}{r['avg_risk_pct']:>9.2f}")
    print(f"\nsummary -> {os.path.relpath(args.out, _REPO_ROOT)}")


if __name__ == "__main__":
    main()
