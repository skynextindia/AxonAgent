#!/usr/bin/env python3
"""Backtest runner using real MT5 candles from the Windows bridge.

Connects to the MT5 bridge WebSocket, requests historical M15 candles
in monthly chunks (bridge caps at ~100 bars/request), then runs the
BacktestEngine on that data — no yFinance, no synthetic data.

Usage:
    python run_bridge_backtest.py [--host 172.x.x.x] [--port 8765]
                                  [--months 6] [--timeframe M15]

Requires the MT5 bridge to be running on Windows.
"""

import argparse
import asyncio
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bridge_bt")


# ── Bridge data collector (paginated) ─────────────────────────────

class BridgeDataCollector:
    """Async WebSocket client that fetches historical candles in chunks."""

    def __init__(self, host: str, port: int, symbol: str = "EURUSD"):
        self.url = f"ws://{host}:{port}"
        self.symbol = symbol
        self.broker_offset = 0

    async def fetch_candles_paginated(
        self, timeframe: str, months: int,
    ) -> list[dict]:
        """Request historical data in monthly chunks and merge results.

        The bridge sends 5 initial broadcast messages on connect (tick,
        regime, account, candles, levels). We drain those first, then
        send our paginated requests and match responses by request_id.
        """
        import websockets

        all_bars: list[dict] = []
        seen_times: set = set()

        now = datetime.now(timezone.utc)

        # Build monthly chunks (oldest first)
        chunks: list[tuple[int, int]] = []
        for i in range(months):
            chunk_end = now - timedelta(days=30 * i)
            chunk_start = now - timedelta(days=30 * (i + 1))
            chunks.append((
                int(chunk_start.timestamp()),
                int(chunk_end.timestamp()),
            ))
        chunks.reverse()

        # Retry connection with backoff
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                ws = await websockets.connect(
                    self.url, ping_interval=20, ping_timeout=10, open_timeout=15,
                )
                break
            except (OSError, TimeoutError, websockets.WebSocketException) as e:
                if attempt == max_retries:
                    raise
                wait = 2 ** attempt
                logger.warning("Connection attempt %d/%d failed: %s — retrying in %ds",
                               attempt, max_retries, e, wait)
                await asyncio.sleep(wait)

        async with ws as ws_ctx:
            # Drain initial broadcast messages (tick, regime, account,
            # candles, levels) so they don't pollute response parsing.
            drain_start = asyncio.get_event_loop().time()
            drained = 0
            while asyncio.get_event_loop().time() - drain_start < 3:
                try:
                    msg = await asyncio.wait_for(ws_ctx.recv(), timeout=0.5)
                    try:
                        j = json.loads(msg)
                        if j.get("type") == "historical":
                            # Unexpected historical — check request_id
                            bars = j.get("bars", [])
                            seen_times.update(b.get("time") for b in bars if b.get("time"))
                            all_bars.extend(bars)
                            logger.info("  ↳ absorbed stray historical (req=%s, %d bars)",
                                        j.get("request_id", "?"), len(bars))
                        else:
                            drained += 1
                    except json.JSONDecodeError:
                        pass
                except asyncio.TimeoutError:
                    break
            if drained:
                logger.info("Drained %d initial broadcast messages", drained)

            for idx, (from_ts, to_ts) in enumerate(chunks):
                req_id = idx + 1
                request = json.dumps({
                    "type": "get_historical",
                    "symbol": self.symbol,
                    "timeframe": timeframe,
                    "from": from_ts,
                    "to": to_ts,
                    "request_id": req_id,
                })
                await ws_ctx.send(request)
                logger.info(
                    "Chunk %d/%d: %s → %s  ",
                    idx + 1, months,
                    datetime.fromtimestamp(from_ts).strftime("%m-%d"),
                    datetime.fromtimestamp(to_ts).strftime("%m-%d"),
                )

                # Wait for a response matching this request_id
                matched = False
                for _ in range(15):  # up to 15 recv attempts per chunk
                    try:
                        resp = await asyncio.wait_for(ws_ctx.recv(), timeout=10)
                    except asyncio.TimeoutError:
                        logger.warning("  ↳ timeout")
                        break

                    try:
                        data = json.loads(resp)
                    except json.JSONDecodeError:
                        continue

                    # Historical response matching our request_id
                    if data.get("type") == "historical" and data.get("request_id") == req_id:
                        self.broker_offset = data.get("broker_offset", 0)
                        bars = data.get("bars", [])
                        if not bars:
                            logger.warning("  ↳ empty chunk")
                        else:
                            chunk_new = 0
                            for b in bars:
                                t = b.get("time")
                                if t not in seen_times:
                                    seen_times.add(t)
                                    all_bars.append(b)
                                    chunk_new += 1
                            logger.info(
                                "  ↳ %d bars (%d new, %d total)",
                                len(bars), chunk_new, len(all_bars),
                            )
                        matched = True
                        break
                    # Ignore non-historical / other request_ids
                if not matched:
                    logger.warning("  ↳ no matching response for req %d", req_id)

        all_bars.sort(key=lambda b: b.get("time", 0))
        logger.info("Total unique bars collected: %d", len(all_bars))
        return all_bars

    @staticmethod
    def to_dataframe(bars: list[dict]) -> "pd.DataFrame":
        """Convert bridge bars to a DataFrame matching BacktestEngine expectations."""
        import pandas as pd

        rows = []
        for b in bars:
            t = b["time"]
            if isinstance(t, int):
                dt = datetime.fromtimestamp(t, tz=timezone.utc)
            else:
                dt = datetime.fromisoformat(str(t))

            rows.append({
                "time": dt,
                "Open": float(b["open"]),
                "High": float(b["high"]),
                "Low": float(b["low"]),
                "Close": float(b["close"]),
                "Volume": int(b.get("volume", 100)),
            })

        df = pd.DataFrame(rows)
        df.set_index("time", inplace=True)
        df.sort_index(inplace=True)
        return df

    @staticmethod
    def seed_engine_levels(engine, bars: list[dict], pip_mult: float = 0.0001):
        """Populate engine.live_evidence.price_levels from candle data.

        The engine's __init__ sets _initialized = True directly,
        bypassing LiveMarketEvidence.initialize(). This means
        _calculate_initial_institutional_levels() is never called and
        price_levels stays empty. When _update_indicators() fires after
        the first H4 candle close, it overwrites swing_highs/swing_lows
        from the empty price_levels -- killing sweep detection.

        We fix this by deriving swing highs/lows, daily highs/lows, and
        round numbers from the bridge data and seeding price_levels.
        """
        from axonai.realtime.live_state import PriceLevel

        if not bars:
            return

        now_utc = datetime.now(timezone.utc)
        n = len(bars)

        # ── Swing highs/lows (fractal over ±5 bars) ──
        swing_highs: list[float] = []
        swing_lows: list[float] = []
        lookback = 5
        for i in range(lookback, n - lookback):
            h = bars[i]["high"]
            l_ = bars[i]["low"]
            if all(h > bars[j]["high"] for j in range(i - lookback, i)) and \
               all(h >= bars[j]["high"] for j in range(i + 1, i + lookback + 1)):
                swing_highs.append(h)
            if all(l_ < bars[j]["low"] for j in range(i - lookback, i)) and \
               all(l_ <= bars[j]["low"] for j in range(i + 1, i + lookback + 1)):
                swing_lows.append(l_)

        def dedup_close(prices, thr=0.00005):
            res = []
            for p in sorted(set(round(x, 5) for x in prices)):
                if not res or abs(p - res[-1]) >= thr:
                    res.append(p)
            return res

        swing_highs = dedup_close(swing_highs, pip_mult * 5)
        swing_lows = dedup_close(swing_lows, pip_mult * 5)

        # ── Group by day → PDH/PDL ──
        daily_highs: dict[str, float] = {}
        daily_lows: dict[str, float] = {}
        for b in bars:
            t = b["time"]
            day_key = (datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
                       if isinstance(t, int) else str(t)[:10])
            daily_highs[day_key] = max(daily_highs.get(day_key, 0), b["high"])
            daily_lows[day_key] = min(daily_lows.get(day_key, float("inf")), b["low"])
        for k in list(daily_lows.keys()):
            if daily_lows[k] == float("inf"):
                daily_lows[k] = 0

        # ── Build PriceLevel list ──
        levels: list = []
        day_keys = sorted(daily_highs.keys())

        # PDH/PDL from previous day
        if len(day_keys) >= 2:
            prev = day_keys[-2]
            levels.append(PriceLevel(round(daily_highs[prev], 5), "PDH", "D1",
                          0, now_utc, "resistance", 0.2, True))
            levels.append(PriceLevel(round(daily_lows[prev], 5), "PDL", "D1",
                          0, now_utc, "support", 0.2, True))

        # Swing highs → resistance
        for price in swing_highs[-8:]:
            levels.append(PriceLevel(price, "H4_SWING", "H4",
                          0, now_utc, "resistance", 0.2, True))
        # Swing lows → support
        for price in swing_lows[-8:]:
            levels.append(PriceLevel(price, "H4_SWING", "H4",
                          0, now_utc, "support", 0.2, True))

        # Round numbers at 50-pip intervals
        all_prices = [b["high"] for b in bars] + [b["low"] for b in bars]
        price_min = min(all_prices)
        price_max = max(all_prices)
        pip50 = 50 * pip_mult
        base = round(price_min / pip50) * pip50
        current_bid = bars[-1]["close"]
        i = -4
        while base + i * pip50 <= price_max + pip50 * 2:
            r_price = base + i * pip50
            if r_price >= price_min - pip50 * 2:
                levels.append(PriceLevel(round(r_price, 5), "ROUND", "SESSION",
                              0, now_utc,
                              "support" if r_price < current_bid else "resistance",
                              0.2, True))
            i += 1

        engine.live_evidence.price_levels = levels
        logger.info("Seeded %d price levels (%d SH, %d SL)",
                     len(levels), len(swing_highs), len(swing_lows))

    @staticmethod
    def to_candles_and_ticks(
        bars: list[dict],
    ) -> tuple[list, list]:
        """Convert bridge bars to (LiveCandle list, interpolated tick list)."""
        import numpy as np
        from axonai.realtime.event_types import LiveCandle

        candles: list[LiveCandle] = []
        ticks: list[tuple[float, float, datetime]] = []

        for b in bars:
            t = b["time"]
            if isinstance(t, int):
                dt = datetime.fromtimestamp(t, tz=timezone.utc)
            else:
                dt = datetime.fromisoformat(str(t))

            o = float(b["open"])
            h = float(b["high"])
            l = float(b["low"])
            c = float(b["close"])

            candle = LiveCandle(
                timeframe="M15",
                open=o, high=h, low=l, close=c,
                volume=int(b.get("volume", 100)),
                open_time=dt,
                is_closed=True,
            )
            candles.append(candle)

            # Interpolate ticks from M15 bar
            step_dt = timedelta(minutes=15) / 15
            is_bullish = c >= o
            sub_prices = [o]
            if is_bullish:
                sub_prices.extend(np.linspace(o, l, 4)[1:])
                sub_prices.extend(np.linspace(l, h, 6)[1:])
                sub_prices.extend(np.linspace(h, c, 5)[1:])
            else:
                sub_prices.extend(np.linspace(o, h, 4)[1:])
                sub_prices.extend(np.linspace(h, l, 6)[1:])
                sub_prices.extend(np.linspace(l, c, 5)[1:])

            for idx, price in enumerate(sub_prices):
                tick_time = dt + step_dt * idx
                ticks.append((price - 0.00005, price + 0.00005, tick_time))

        ticks.sort(key=lambda x: x[2])
        return candles, ticks


# ── Per-month breakdown builder ────────────────────────────────────

def build_monthly_breakdown(trades: list[dict]) -> list[dict]:
    """Group trades by calendar month and compute WR + PF per month."""
    monthly: dict[str, dict] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "losses": 0, "gross_profit": 0.0, "gross_loss": 0.0}
    )

    for t in trades:
        et = t.get("entry_time")
        if not et:
            continue
        month_key = et.strftime("%Y-%m")
        m = monthly[month_key]
        m["trades"] += 1
        pips = t.get("pips", 0.0)
        if t.get("status") == "WIN":
            m["wins"] += 1
            m["gross_profit"] += pips
        else:
            m["losses"] += 1
            m["gross_loss"] += abs(pips)

    breakdown = []
    for month_key in sorted(monthly.keys()):
        m = monthly[month_key]
        wr = (m["wins"] / m["trades"] * 100) if m["trades"] else 0.0
        pf = (m["gross_profit"] / m["gross_loss"]) if m["gross_loss"] > 0 else float("inf")
        net = m["gross_profit"] - m["gross_loss"]
        breakdown.append({
            "month": month_key,
            "trades": m["trades"],
            "wins": m["wins"],
            "losses": m["losses"],
            "win_rate": round(wr, 1),
            "profit_factor": round(pf, 2),
            "net_pips": round(net, 1),
            "gross_profit": round(m["gross_profit"], 1),
            "gross_loss": round(m["gross_loss"], 1),
        })
    return breakdown


# ── Main ───────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Backtest on MT5 bridge candles")
    parser.add_argument("--host", default=None, help="Bridge host (auto-detect)")
    parser.add_argument("--port", type=int, default=8765, help="Bridge WebSocket port")
    parser.add_argument("--symbol", default="EURUSD", help="Symbol to backtest")
    parser.add_argument("--months", type=int, default=6, help="Months of historical data")
    parser.add_argument("--timeframe", default="M15", choices=["M15", "M1", "M5", "H1", "H4"])
    args = parser.parse_args()

    # Auto-detect bridge host
    if args.host is None:
        import subprocess
        try:
            result = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True, text=True, timeout=5,
            )
            args.host = result.stdout.strip().split()[2]
        except Exception:
            args.host = "172.28.176.1"

    logger.info("=" * 65)
    logger.info("  MT5 BRIDGE BACKTEST — %s %s (%d months)", args.symbol, args.timeframe, args.months)
    logger.info("  Bridge: ws://%s:%d", args.host, args.port)
    logger.info("=" * 65)

    # ── Step 1: Fetch candles (paginated) ─────────────────────────
    collector = BridgeDataCollector(args.host, args.port, args.symbol)
    bars = await collector.fetch_candles_paginated(args.timeframe, args.months)

    if not bars:
        logger.error("No data received from bridge. Is it running on Windows?")
        sys.exit(1)

    logger.info("Fetched %d %s bars for %s across %d months",
                len(bars), args.timeframe, args.symbol, args.months)

    # ── Step 2: Monkey-patch MT5 module ───────────────────────────
    import axonai.dataflows.mt5_data as mt5_mod
    import axonai.realtime.backtester as bt_mod

    candles_list, ticks_list = BridgeDataCollector.to_candles_and_ticks(bars)
    df = BridgeDataCollector.to_dataframe(bars)

    def patched_fetch_bars(symbol, timeframe, from_date, to_date):
        return df

    def patched_init(*args, **kwargs):
        return True

    def patched_load_historical_data(self):
        return candles_list, ticks_list

    mt5_mod.mt5_initialize = patched_init
    mt5_mod._fetch_bars = patched_fetch_bars
    mt5_mod._to_mt5_symbol = lambda ticker, config=None: ticker
    mt5_mod._ensure_symbol_visible = lambda sym: None
    bt_mod.mt5_initialize = patched_init
    bt_mod._fetch_bars = patched_fetch_bars
    bt_mod._ensure_symbol_visible = lambda sym: None
    bt_mod._to_mt5_symbol = lambda ticker, config=None: ticker
    bt_mod.BacktestEngine.load_historical_data = patched_load_historical_data

    # ── Step 3: Run backtest ──────────────────────────────────────
    from axonai.realtime.backtester import BacktestEngine

    # Determine pip multiplier based on symbol
    is_gold = "XAU" in args.symbol.upper() or "GOLD" in args.symbol.upper()
    pip_mult = 0.1 if is_gold else 0.01 if "JPY" in args.symbol.upper() else 0.0001

    engine = BacktestEngine(
        ticker=args.symbol.replace("EURUSD", "EURUSD=X"),
        days=args.months * 30,
    )
    engine.pip_mult = pip_mult
    engine.event_detector._pip_mult = pip_mult
    engine.event_detector.set_pip_multiplier = lambda is_jpy: None
    engine.event_detector.peak_detector_base.pip_mult = pip_mult
    engine.event_detector.peak_detector_opt.pip_mult = pip_mult
    engine.event_detector.peak_detector.pip_mult = pip_mult

    # Seed price_levels before run() so _update_indicators doesn't
    # overwrite swing_highs/swing_lows with empty lists.
    BridgeDataCollector.seed_engine_levels(engine, bars, pip_mult=pip_mult)

    logger.info("Running backtest on %d %s bars (%d months)...",
                len(bars), args.timeframe, args.months)
    report = engine.run()

    trades: list[dict] = getattr(engine, "simulated_trades", [])

    # Shift all trade timestamps to Broker Server Time for exact chart matching
    offset_seconds = getattr(collector, "broker_offset", 0)
    if abs(offset_seconds) >= 24 * 3600:
        offset_seconds = (offset_seconds + 12 * 3600) % (24 * 3600) - 12 * 3600
    broker_tz = timezone(timedelta(seconds=offset_seconds))
    for t in trades:
        if t.get("entry_time"):
            et = t["entry_time"]
            if et.tzinfo is None:
                et = et.replace(tzinfo=timezone.utc)
            t["entry_time"] = et.astimezone(broker_tz)
        if t.get("exit_time"):
            ext = t["exit_time"]
            if ext.tzinfo is None:
                ext = ext.replace(tzinfo=timezone.utc)
            t["exit_time"] = ext.astimezone(broker_tz)

    # ── Step 4: Per-month breakdown ───────────────────────────────
    monthly = build_monthly_breakdown(trades)

    # ── Step 5: Save report ───────────────────────────────────────
    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = out_dir / f"bridge_bt_{args.symbol}_{args.months}m_{ts}.md"
    json_path = out_dir / f"bridge_bt_{args.symbol}_{args.months}m_{ts}.json"

    lines = [
        f"# AxonAI Bridge Backtest — {args.symbol}",
        f"**Execution**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Data**: MT5 bridge · {args.timeframe} · {args.months} months",
        f"**Bars**: {len(bars)}",
        "",
        "## Overall Performance",
        f"| Metric | Value |",
        f"| :--- | :--- |",
        f"| **Total Trades** | {report['total_trades']} |",
        f"| **Wins** | {report['wins']} ✅ |",
        f"| **Losses** | {report['losses']} ❌ |",
        f"| **Win Rate** | **{report['win_rate_percent']:.1f}%** |",
        f"| **Net P&L** | **{report['net_profit_pips']:+.1f} pips** |",
        f"| **Profit Factor** | {report['profit_factor']:.2f} |",
        "",
        "## Per-Month Breakdown",
        "| Month | Trades | Wins | Losses | Win Rate | Profit Factor | Net Pips |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for m in monthly:
        lines.append(
            f"| {m['month']} | {m['trades']} | {m['wins']} | {m['losses']} "
            f"| {m['win_rate']}% | {m['profit_factor']} | {m['net_pips']:+.1f} |"
        )

    # Totals row
    tot_t = sum(m["trades"] for m in monthly)
    tot_w = sum(m["wins"] for m in monthly)
    tot_l = sum(m["losses"] for m in monthly)
    tot_net = sum(m["net_pips"] for m in monthly)
    tot_gp = sum(m.get("gross_profit", 0) for m in monthly)
    tot_gl = sum(m.get("gross_loss", 0) for m in monthly)
    lines.append(
        f"| **Total** | **{tot_t}** | **{tot_w}** | **{tot_l}** "
        f"| **{(tot_w/tot_t*100) if tot_t else 0:.1f}%** "
        f"| **{(tot_gp/tot_gl) if tot_gl else '∞'}** "
        f"| **{tot_net:+.1f}** |"
    )

    lines += [
        "",
        "## Trade Log",
        "| ID | Dir | Entry Time | Entry Price | Exit Time | Exit Price | SL | TP | Pips | Signal | Exit Reason | Status |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :---: | :--- | :--- | :--- |",
    ]
    for t in trades:
        status = "✅ WIN" if t.get("status") == "WIN" else "❌ LOSS"
        sl = t.get("sl")
        tp = t.get("tp")
        sl_str = f"{sl:.5f}" if sl else "—"
        tp_str = f"{tp:.5f}" if tp else "—"
        reason = t.get("close_reason", "—")
        if "Stop Loss" in reason:
            reason_str = "🛑 SL Hit"
        elif "Take Profit" in reason:
            reason_str = "🎯 TP Hit"
        elif "End of Day" in reason or "Session Close" in reason:
            reason_str = "⏰ EOD Close"
        else:
            reason_str = reason

        lines.append(
            f"| {t.get('id', '?')} "
            f"| {t.get('direction', '?')} "
            f"| {t['entry_time'].strftime('%d-%m-%y %H:%M') if t.get('entry_time') else '—'} "
            f"| {t.get('entry_price', 0):.5f} "
            f"| {t['exit_time'].strftime('%d-%m-%y %H:%M') if t.get('exit_time') else '—'} "
            f"| {t.get('exit_price', 0):.5f} "
            f"| {sl_str} "
            f"| {tp_str} "
            f"| {t.get('pips', 0):+.1f} "
            f"| {t.get('trigger', '—')} "
            f"| {reason_str} "
            f"| {status} |"
        )

    lines += [
        "",
        "## Events Detected",
        "| Event Type | Count |",
        "| :--- | :--- |",
    ]
    for ev_type, count in sorted(
        report.get("event_breakdown", {}).items(), key=lambda x: -x[1]
    ):
        lines.append(f"| `{ev_type}` | {count} |")

    if trades:
        lines += ["", "## Trade Details"]
        for t in trades:
            sl = t.get("sl")
            tp = t.get("tp")
            sl_str = f"SL={sl:.5f}" if sl else "no SL"
            tp_str = f"TP={tp:.5f}" if tp else "no TP"
            lines.append(
                f"- **#{t.get('id', '?')}** {t.get('direction', '?')} "
                f"@ {t.get('entry_price', 0):.5f} → {t.get('exit_price', 0):.5f} "
                f"({t.get('pips', 0):+.1f} pips, {t.get('status', '?')}) "
                f"| {sl_str} {tp_str} | {t.get('close_reason', '—')}"
            )

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    json_report = {
        "ticker": args.symbol,
        "months": args.months,
        "timeframe": args.timeframe,
        "bars": len(bars),
        "total_trades": report["total_trades"],
        "wins": report["wins"],
        "losses": report["losses"],
        "win_rate_percent": report["win_rate_percent"],
        "net_profit_pips": report["net_profit_pips"],
        "profit_factor": report["profit_factor"],
        "monthly_breakdown": monthly,
        "event_breakdown": report.get("event_breakdown", {}),
    }
    with open(json_path, "w") as f:
        json.dump(json_report, f, indent=2, default=str)

    logger.info("Markdown report → %s", md_path)
    logger.info("JSON summary   → %s", json_path)

    # ── Console output ────────────────────────────────────────────
    print()
    print("=" * 65)
    print(f"  BRIDGE BACKTEST — {args.symbol} {args.timeframe} ({args.months} months)")
    print(f"  Data: {len(bars)} bars from MT5 bridge (real)")
    print("=" * 65)
    print(f"  Total Trades:  {report['total_trades']}")
    print(f"  Wins / Losses: {report['wins']} / {report['losses']}")
    print(f"  Win Rate:      {report['win_rate_percent']:.1f}%")
    print(f"  Net P&L:       {report['net_profit_pips']:+.1f} pips")
    print(f"  Profit Factor: {report['profit_factor']:.2f}")
    print()
    print("  ── Per-Month ──")
    print(f"  {'Month':<10} {'Trades':<8} {'WR':<8} {'PF':<8} {'Net':<10}")
    print(f"  {'─'*8:<10} {'─'*6:<8} {'─'*6:<8} {'─'*6:<8} {'─'*8:<10}")
    for m in monthly:
        print(f"  {m['month']:<10} {m['trades']:<8} {m['win_rate']:<7}% {m['profit_factor']:<7} {m['net_pips']:+.1f}")
    print(f"  {'─'*44}")
    print(f"  {'TOTAL':<10} {tot_t:<8} {((tot_w/tot_t*100) if tot_t else 0):<7.1f}% "
          f"{(tot_gp/tot_gl if tot_gl else 0):<7} {tot_net:+.1f}")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
