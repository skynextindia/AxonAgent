"""NautilusTrader backtest of the chart-pattern breakout 1R bracket.

Research spike (2026-08-12). Runs the SAME validated geometry
(axonai.realtime.chart_patterns) that produced the offline +6.06p/trade OOS
result, but through Nautilus's deterministic engine with a modeled fill
(FillModel slippage + bar execution) instead of the offline sim's idealized
neckline fill. Purpose: see whether the edge survives a realistic fill BEFORE
trusting the live daemon's real-money measurement.

Live stack untouched: separate venv (.venv_nautilus), separate dir, reads only
the historical parquet bars from prep_bars.py.

Run:
    .venv_nautilus\\Scripts\\python research\\nautilus_spike\\breakout_backtest.py
"""
import os
import sys
from decimal import Decimal

import pandas as pd

# Reuse the validated pattern geometry from the live repo. Load the module by
# FILE PATH so the axonai package __init__ (which pulls MT5/requests) never runs.
import importlib.util  # noqa: E402
_cp_path = r"D:\AXON.AI\AxonAgent-Agy\axonai\realtime\chart_patterns.py"
_spec = importlib.util.spec_from_file_location("chart_patterns", _cp_path)
_cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cp)
_zigzag, _candidates, _first_break = _cp._zigzag, _cp._candidates, _cp._first_break

from nautilus_trader.backtest.engine import BacktestEngine  # noqa: E402
from nautilus_trader.backtest.models import FillModel  # noqa: E402
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig  # noqa: E402
from nautilus_trader.model.currencies import USD  # noqa: E402
from nautilus_trader.model.data import BarType  # noqa: E402
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, OrderType, TimeInForce  # noqa: E402
from nautilus_trader.model.identifiers import Venue  # noqa: E402
from nautilus_trader.model.objects import Money  # noqa: E402
from nautilus_trader.persistence.wranglers import BarDataWrangler  # noqa: E402
from nautilus_trader.test_kit.providers import TestInstrumentProvider  # noqa: E402
from nautilus_trader.trading.strategy import Strategy  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data")
VENUE = Venue("SIM")
PAIRS = {"EURUSD": "EUR/USD", "USDJPY": "USD/JPY", "AUDUSD": "AUD/USD"}
TIME_STOP_BARS = 60          # sim's OUTW scratch window
MAX_DRIFT_PIPS = 1.5         # live adverse-drift gate
QTY = 100_000                # 1 lot; per-trade pip expectancy is what we compare


class BreakoutConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    pip: float
    thr_pips: float


class BreakoutStrategy(Strategy):
    def __init__(self, config: BreakoutConfig):
        super().__init__(config)
        self.bar_type = BarType.from_str(config.bar_type)
        self.instrument = None
        self.pip = config.pip
        self.thr = config.thr_pips * config.pip
        self.S = []
        self.fired = set()
        self.entry_bar_index = None
        self.dbg = {"bars": 0, "candidates": 0, "breaks_at_last": 0, "submits": 0}

    def on_start(self):
        self.instrument = self.cache.instrument(self.bar_type.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        self.dbg["bars"] += 1
        epoch = int(bar.ts_event // 1_000_000_000)
        self.S.append([float(bar.open), float(bar.high), float(bar.low), float(bar.close), epoch])
        if len(self.S) > 500:
            self.S = self.S[-500:]

        # 15h time-stop on any open position (mirror sim's 60-bar scratch).
        if self.portfolio.is_net_long(self.instrument.id) or self.portfolio.is_net_short(self.instrument.id):
            if self.entry_bar_index is not None and (len(self.S) - self.entry_bar_index) >= TIME_STOP_BARS:
                self.close_all_positions(self.instrument.id)
                self.entry_bar_index = None
            return  # one position at a time

        if len(self.S) < 20:
            return
        last = len(self.S) - 1
        piv = _zigzag(self.S, self.thr)
        for typ, direction, down, neck, target, sl, frm in _candidates(piv, self.S):
            self.dbg["candidates"] += 1
            b = _first_break(self.S, frm, neck, down)
            if b is None or b != last:
                continue
            self.dbg["breaks_at_last"] += 1
            key = (typ, self.S[b][4])
            if key in self.fired:
                continue
            risk_price = abs(neck - sl)
            risk_pips = risk_price / self.pip
            if risk_pips < 1.0 or risk_pips > 60.0 or abs(neck - target) / self.pip < 0.5:
                continue
            close = self.S[last][3]
            drift = ((neck - close) if down else (close - neck)) / self.pip
            if drift > MAX_DRIFT_PIPS:
                continue
            self.fired.add(key)
            tp = neck - risk_price if down else neck + risk_price
            side = OrderSide.SELL if down else OrderSide.BUY
            entry_mode = os.environ.get("ENTRY_MODE", "market").lower()
            if entry_mode == "limit":
                # Enter AT the neckline (matches the offline sim). Isolates the
                # fill-drift effect; may not fill if price runs away (realistic miss).
                bracket = self.order_factory.bracket(
                    instrument_id=self.instrument.id, order_side=side,
                    quantity=self.instrument.make_qty(QTY),
                    entry_order_type=OrderType.LIMIT,
                    entry_price=self.instrument.make_price(neck),
                    time_in_force=TimeInForce.GTC,
                    sl_trigger_price=self.instrument.make_price(sl),
                    tp_price=self.instrument.make_price(tp),
                )
            else:
                bracket = self.order_factory.bracket(
                    instrument_id=self.instrument.id, order_side=side,
                    quantity=self.instrument.make_qty(QTY),
                    sl_trigger_price=self.instrument.make_price(sl),
                    tp_price=self.instrument.make_price(tp),
                )
            self.submit_order_list(bracket)
            self.dbg["submits"] += 1
            self.entry_bar_index = last
            return


def run_pair(sym: str, fx: str) -> pd.DataFrame:
    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id="BREAKOUT-001",
        logging=LoggingConfig(bypass_logging=True),
    ))
    fill = FillModel(prob_fill_on_limit=0.9, prob_slippage=0.5, random_seed=42)
    engine.add_venue(
        venue=VENUE, oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
        base_currency=USD, starting_balances=[Money(1_000_000, USD)],
        fill_model=fill,
    )
    instrument = TestInstrumentProvider.default_fx_ccy(fx, VENUE)
    engine.add_instrument(instrument)

    pip = 0.01 if "JPY" in sym else 0.0001
    thr = 12.0 if "JPY" in sym else 8.0
    half_spread = 0.3 * pip  # ~0.6p round-trip modeled spread cost

    df = pd.read_parquet(os.path.join(DATA, f"{sym}_m15.parquet"))
    # Two-sided book from mid bars: BID=mid-half, ASK=mid+half. Gives the sim
    # exchange a real market so market/stop/limit fill WITH a spread cost.
    ohlc = ["open", "high", "low", "close"]
    bid_df = df.copy(); bid_df[ohlc] = df[ohlc] - half_spread
    ask_df = df.copy(); ask_df[ohlc] = df[ohlc] + half_spread
    bar_type = f"{instrument.id}-15-MINUTE-BID-EXTERNAL"
    ask_bt = f"{instrument.id}-15-MINUTE-ASK-EXTERNAL"
    engine.add_data(BarDataWrangler(BarType.from_str(bar_type), instrument).process(bid_df))
    engine.add_data(BarDataWrangler(BarType.from_str(ask_bt), instrument).process(ask_df))

    strat = BreakoutStrategy(BreakoutConfig(
        instrument_id=str(instrument.id), bar_type=bar_type, pip=pip, thr_pips=thr,
    ))
    engine.add_strategy(strat)
    engine.run()
    print(f"  [dbg] {strat.dbg}  fills={len(engine.trader.generate_order_fills_report())}")
    report = engine.trader.generate_positions_report()
    engine.dispose()
    if report is None or len(report) == 0:
        return pd.DataFrame()
    report["sym"] = sym
    report["pip"] = pip
    return report


def summarize(rep: pd.DataFrame):
    if rep.empty:
        print("  no trades"); return
    op = rep["avg_px_open"].astype(float)
    cl = rep["avg_px_close"].astype(float)
    sgn = rep["side"].map(lambda s: 1.0 if str(s).upper() == "LONG" else -1.0)
    pips = (cl - op) / rep["pip"].astype(float) * sgn
    pnl = rep["realized_pnl"].astype(str).str.replace(r"[^\d.-]", "", regex=True).astype(float)
    n = len(rep)
    win = (pips > 0).sum()
    print(f"  n={n}  win={100*win/n:.1f}%  exp={pips.mean():+.2f}p  "
          f"median={pips.median():+.2f}p  total={pips.sum():+.0f}p  pnl_usd={pnl.sum():+.0f}")


def main():
    allrep = []
    for sym, fx in PAIRS.items():
        print(f"=== {sym} ({fx}) ===")
        rep = run_pair(sym, fx)
        summarize(rep)
        if not rep.empty:
            allrep.append(rep)
    if allrep:
        combined = pd.concat(allrep, ignore_index=True)
        print("=== ALL (ex-GBP) ===")
        summarize(combined)
        print("\nOffline OOS reference: +6.06p/trade ex-GBP (t=3.10, n=89), idealized neckline fill.")


if __name__ == "__main__":
    main()
