"""Exit Statistics Collector.

Records every exit event with reason, pips, phase, confidence, and
energy state. Used to understand exit behaviour before tuning thresholds.
Do not tune thresholds until this has run on real-tick data.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class ExitRecord:
    timestamp: str
    reason: str
    pips: float
    phase: str
    confidence: float
    energy_state: str
    pips_profit_at_exit: float


class ExitStats:
    def __init__(self, csv_path: Optional[str] = None):
        self._records: List[ExitRecord] = []
        self._csv_path = csv_path
        if self._csv_path:
            p = Path(self._csv_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            # Write header if file does not exist or is empty
            if not p.exists() or p.stat().st_size == 0:
                with open(p, "w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "timestamp", "reason", "pips", "phase",
                        "confidence", "energy_state", "pips_profit_at_exit"
                    ])

    def record(
        self,
        reason: str,
        pips: float,
        phase: str,
        confidence: float,
        energy_state: str,
        pips_profit_at_exit: float,
    ) -> None:
        rec = ExitRecord(
            timestamp=datetime.utcnow().isoformat(),
            reason=reason,
            pips=round(pips, 2),
            phase=phase,
            confidence=round(confidence, 1),
            energy_state=energy_state,
            pips_profit_at_exit=round(pips_profit_at_exit, 2),
        )
        self._records.append(rec)

        if self._csv_path:
            self._append_record_to_csv(self._csv_path, rec)

    def _append_record_to_csv(self, path: str, r: ExitRecord) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        file_exists = p.exists() and p.stat().st_size > 0
        with open(p, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(r).keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerow(asdict(r))

    def summary(self) -> dict:
        if not self._records:
            return {}
        by_reason: dict = {}
        for r in self._records:
            grp = by_reason.setdefault(r.reason, {"count": 0, "total_pips": 0.0, "wins": 0})
            grp["count"] += 1
            grp["total_pips"] += r.pips
            if r.pips > 0:
                grp["wins"] += 1
        for grp in by_reason.values():
            grp["avg_pips"] = round(grp["total_pips"] / grp["count"], 2)
            grp["win_rate"] = round(grp["wins"] / grp["count"] * 100, 1)
        return by_reason

    def to_csv(self, path: str) -> None:
        if not self._records:
            return
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(self._records[0]).keys()))
            writer.writeheader()
            writer.writerows(asdict(r) for r in self._records)

    def to_json(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.summary(), f, indent=2)


__all__ = ["ExitStats", "ExitRecord"]
