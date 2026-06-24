"""Trade Analytics Tracker.

Captures comprehensive pre-trade context and post-trade outcomes for
continuous strategy evaluation. This allows us to answer questions like:
"How do TRAP entries perform in COMPRESSION regimes?"
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime

from axonai.realtime.reversal_model import EngineSnapshot


@dataclass
class TradeRecord:
    """Complete lifecycle record of a single trade."""
    ticket: int
    symbol: str
    direction: str
    entry_time: str
    entry_price: float
    initial_sl: float
    initial_tp: float
    
    # Pre-trade Context (from EngineSnapshot)
    regime: str
    regime_confidence: float
    mtf_alignment: float
    mtf_context: str
    anomaly_velocity_z: float
    displacement_classification: str
    nearest_support: float
    nearest_resistance: float
    
    # Post-trade Outcome
    exit_time: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    pips_profit: float = 0.0
    max_favorable_excursion: float = 0.0
    time_in_drawdown_sec: float = 0.0
    health_score_at_exit: float = 0.0


class TradeAnalytics:
    """Records trade history for off-line evaluation."""

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
    ) -> None:
        """Create a new trade record with pre-trade context."""
        support_price = snapshot.liquidity.nearest_support.price if snapshot.liquidity.nearest_support else 0.0
        resistance_price = snapshot.liquidity.nearest_resistance.price if snapshot.liquidity.nearest_resistance else 0.0
        
        record = TradeRecord(
            ticket=ticket,
            symbol=symbol,
            direction=direction,
            entry_time=datetime.now().isoformat(),
            entry_price=entry_price,
            initial_sl=sl,
            initial_tp=tp,
            regime=snapshot.regime.regime,
            regime_confidence=snapshot.regime.confidence,
            mtf_alignment=snapshot.mtf.alignment_score,
            mtf_context=snapshot.mtf.context_summary,
            anomaly_velocity_z=snapshot.velocity.z_score,
            displacement_classification=snapshot.displacement.classification,
            nearest_support=support_price,
            nearest_resistance=resistance_price,
        )
        self._active_trades[ticket] = record

    def record_exit(
        self,
        ticket: int,
        exit_price: float,
        pips_profit: float,
        exit_reason: str,
        snapshot: EngineSnapshot,
    ) -> None:
        """Complete the trade record and write to disk."""
        if ticket not in self._active_trades:
            return
            
        record = self._active_trades[ticket]
        record.exit_time = datetime.now().isoformat()
        record.exit_price = exit_price
        record.pips_profit = pips_profit
        record.exit_reason = exit_reason
        
        record.max_favorable_excursion = snapshot.trade_health.max_favorable_excursion
        record.time_in_drawdown_sec = snapshot.trade_health.time_in_drawdown_sec
        record.health_score_at_exit = snapshot.trade_health.score
        
        self._write_record(record)
        del self._active_trades[ticket]

    def _write_record(self, record: TradeRecord) -> None:
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(record)) + "\n")
        except Exception as e:
            pass # Failsafe against I/O errors interrupting the daemon


__all__ = ["TradeAnalytics", "TradeRecord"]
