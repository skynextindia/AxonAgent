"""Macro bridge: connects System 2 (macro agent) decisions to System 1 (microstructure).

Reads the latest macro decision from reports/signals_{date}.jsonl (Option A — decoupled).
Exposes a MacroSignal snapshot for MarketContextBuilder to consume.
"""

import json
import os
import time
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MacroSignal:
    """Latest macro-level decision from System 2."""
    bias: str           # "BUY" | "SELL" | "HOLD"
    confidence: float   # 0.0–1.0
    key_level: float    # nearest macro support/resistance price
    age_sec: float      # seconds since this decision was produced


class MacroBridge:
    """Reads System 2 macro decisions and exposes them to System 1.

    Data source: latest line in reports/signals_{date}.jsonl (decoupled, file-based).
    Call `refresh()` periodically (e.g. every 30s) to re-read the file.
    Call `snapshot()` to get the latest MacroSignal (or None if no data).
    """

    def __init__(self, reports_dir: str = "reports"):
        self._reports_dir = reports_dir
        self._latest: Optional[MacroSignal] = None
        self._updated_at: float = 0.0
        self._last_file_mtime: float = 0.0

    def update(self, bias: str, confidence: float, key_level: float) -> None:
        """Manually push a macro signal (used when System 2 runs in-process)."""
        self._updated_at = time.time()
        self._latest = MacroSignal(
            bias=bias.upper(),
            confidence=max(0.0, min(1.0, confidence)),
            key_level=key_level,
            age_sec=0.0
        )

    def refresh(self) -> None:
        """Re-read the latest signal from the daily signals JSONL file."""
        date_str = datetime.now().strftime("%Y%m%d")
        jsonl_path = os.path.join(self._reports_dir, f"signals_{date_str}.jsonl")

        if not os.path.exists(jsonl_path):
            # Fallback to non-rotated legacy file
            legacy_path = os.path.join(self._reports_dir, "signals.jsonl")
            if os.path.exists(legacy_path):
                jsonl_path = legacy_path
            else:
                return

        try:
            mtime = os.path.getmtime(jsonl_path)
            if mtime == self._last_file_mtime:
                return  # No new data
            self._last_file_mtime = mtime

            # Read last line efficiently
            last_line = ""
            with open(jsonl_path, "rb") as f:
                # Seek to end, walk backwards to find last newline
                f.seek(0, 2)
                fsize = f.tell()
                if fsize == 0:
                    return
                pos = fsize - 1
                while pos > 0:
                    f.seek(pos)
                    ch = f.read(1)
                    if ch == b"\n" and pos < fsize - 1:
                        break
                    pos -= 1
                last_line = f.read().decode("utf-8").strip()

            if not last_line:
                return

            record = json.loads(last_line)
            decision = record.get("decision", "")

            # Parse bias from decision string (e.g. "BUY", "SELL", "HOLD", or structured dict)
            if isinstance(decision, dict):
                bias = decision.get("signal", decision.get("action", "HOLD")).upper()
                confidence = float(decision.get("confidence", 0.5))
                key_level = float(decision.get("key_level", 0.0))
            elif isinstance(decision, str):
                bias = decision.upper() if decision.upper() in ("BUY", "SELL", "HOLD") else "HOLD"
                confidence = 0.5
                key_level = float(record.get("event_price", 0.0))
            else:
                return

            # Parse timestamp for age calculation
            ts_str = record.get("timestamp", "")
            try:
                decision_time = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                age_sec = (datetime.now() - decision_time).total_seconds()
            except (ValueError, TypeError):
                age_sec = 999.0

            self._updated_at = time.time()
            self._latest = MacroSignal(
                bias=bias,
                confidence=confidence,
                key_level=key_level,
                age_sec=age_sec
            )
        except Exception as e:
            logger.warning("MacroBridge refresh failed: %s", e)

    def snapshot(self) -> Optional[MacroSignal]:
        """Return the latest macro signal with updated age, or None if no data."""
        if self._latest is None:
            return None
        age = time.time() - self._updated_at
        return MacroSignal(
            bias=self._latest.bias,
            confidence=self._latest.confidence,
            key_level=self._latest.key_level,
            age_sec=age
        )
