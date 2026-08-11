"""Live chart-pattern breakout entry (the OOS-validated 1R-bracket signal).

2026-08-12 validation (shadow_patterns.csv, 227 resolved paper trades):
out-of-sample (>=2026-07-21) the 1R bracket earned +2.70p/trade across all four
FX pairs (t=1.68, n=142) and +6.06p/trade excluding GBPUSD (t=3.10, n=89),
persistent across W29-W32 and both trend/chop regimes, robust to +1.5p extra
slippage. GBPUSD was -2.94p OOS and is therefore excluded by default. The tick
fade entry was falsified on the same data (see memory: axonai-sweeps-lead).

This detector runs the EXACT geometry the validation used (shared module
axonai.realtime.chart_patterns) over the live M15 candle deque and fires when
the JUST-CLOSED bar is the first close through a pattern neckline:

    entry  = neckline (market order on the close of the break bar;
             fill drift vs the neckline is the slippage the stress test covered)
    SL     = structural pattern extreme (defines R)
    TP     = neckline +/- 1R  (computed from the NECKLINE, matching the sim)

The daemon places it as a pure broker-side bracket: no trailing, no engine
thesis exits -- only broker SL/TP plus a 15h time-stop (= the sim's 60-bar
scratch window). Deviating from that bracket invalidates the measured edge.
"""
from dataclasses import dataclass
from datetime import timezone
from typing import Optional

from axonai.realtime.chart_patterns import _zigzag, _candidates, _first_break, _eff_ratio

DEFAULT_SYMBOLS = ["EURUSD", "USDJPY", "AUDUSD"]  # GBPUSD excluded: -2.94p OOS


@dataclass
class BreakoutSignal:
    symbol: str
    pattern_type: str
    direction: str        # "BUY" | "SELL"
    entry: float          # neckline level
    sl: float             # structural pattern extreme
    tp: float             # entry +/- risk (fixed 1R)
    risk_pips: float
    er: float             # Kaufman efficiency ratio at break (diagnostic)
    break_epoch: int      # open-time epoch of the break bar (dedup key)


class PatternBreakoutDetector:
    """Stateful per-symbol detector. Call on_m15_close() after every M15 close."""

    def __init__(self, symbol: str, config: Optional[dict] = None):
        self.symbol = symbol.strip().upper()
        self.config = config or {}
        self._pip = 0.01 if ("JPY" in self.symbol or "XAU" in self.symbol) else 0.0001
        # Same zigzag threshold the offline miner used per symbol.
        thr_pips = 12.0 if "JPY" in self.symbol else 8.0
        self._thr = thr_pips * self._pip
        allowed = self.config.get("pattern_breakout_symbols", DEFAULT_SYMBOLS)
        self._allowed = {str(s).strip().upper() for s in allowed}
        # Broker symbols may carry a suffix (EURUSDm, EURUSD.et). Match on the
        # clean-pair prefix so a suffixed broker cannot silently disable the
        # only active entry path.
        self._is_allowed = any(self.symbol.startswith(a) for a in self._allowed)
        self._min_risk = float(self.config.get("pattern_breakout_min_risk_pips", 1.0))
        self._max_risk = float(self.config.get("pattern_breakout_max_risk_pips", 60.0))
        self._fired: set = set()   # {(pattern_type, break_epoch)}

    @property
    def enabled(self) -> bool:
        return self._is_allowed

    def on_m15_close(self, candles) -> Optional[BreakoutSignal]:
        """candles: iterable of LiveCandle (closed M15 bars, chronological).

        Returns at most ONE signal, and only when the newest bar is the first
        close through a neckline (b == last). Historical breaks in the warm-up
        backfill can never fire because their break bar is not the newest bar.
        """
        if not self.enabled:
            return None
        S = []
        for c in candles:
            try:
                epoch = int(c.open_time.replace(tzinfo=timezone.utc).timestamp())
                S.append([float(c.open), float(c.high), float(c.low), float(c.close), epoch])
            except (AttributeError, TypeError, ValueError):
                continue
        if len(S) < 20:
            return None
        last = len(S) - 1
        piv = _zigzag(S, self._thr)
        for typ, direction, down, neck, target, sl, frm in _candidates(piv, S):
            b = _first_break(S, frm, neck, down)
            if b is None or b != last:
                continue
            key = (typ, S[b][4])
            if key in self._fired:
                continue
            risk_price = abs(neck - sl)
            risk = risk_price / self._pip
            reward = abs(neck - target) / self._pip
            # Offline miner required risk/reward >= 0.5p; live adds a floor so a
            # stop tighter than the spread can never trade, plus a sanity cap.
            if risk < self._min_risk or risk > self._max_risk or reward < 0.5:
                continue
            self._fired.add(key)
            self._prune_fired(S[last][4])
            tp = neck - risk_price if down else neck + risk_price
            return BreakoutSignal(
                symbol=self.symbol, pattern_type=typ, direction=direction,
                entry=round(neck, 5), sl=round(sl, 5), tp=round(tp, 5),
                risk_pips=round(risk, 1), er=round(_eff_ratio(S, last), 4),
                break_epoch=S[last][4],
            )
        return None

    def _prune_fired(self, newest_epoch: int) -> None:
        """Drop dedup keys older than 3 days; bounds memory over long runs."""
        cutoff = newest_epoch - 3 * 86400
        if len(self._fired) > 256:
            self._fired = {k for k in self._fired if k[1] >= cutoff}
