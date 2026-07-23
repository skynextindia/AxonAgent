"""Reconcile MT5 execution history against reports/trade_analytics.jsonl.

Read-only. Opens a second MT5 connection to the EXECUTION terminal, pulls closed
positions, and joins them to the engine's own trade log by ticket.

Why this exists: until the broker-close fix, record_exit was only called from the
daemon's own close paths, so any position the BROKER closed (take-profit, stop-loss,
stop-out) never reached trade_analytics.jsonl. The log was a censored sample biased
against exactly the trades that ran longest. This tool shows the true picture, and
still serves afterwards as the check that the log and the broker agree.

Usage:
    python -m axonai.scripts.reconcile_trades
    python -m axonai.scripts.reconcile_trades --days 7
    python -m axonai.scripts.reconcile_trades --date 2026-07-23
"""
import argparse
import collections
import datetime
import json
import os

DEFAULT_EXEC_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
DEFAULT_LOG = os.path.join("reports", "trade_analytics.jsonl")

# MT5 deal.reason
REASON = {0: "CLIENT", 1: "MOBILE", 2: "WEB", 3: "EXPERT", 4: "SL", 5: "TP", 6: "SO", 7: "ROLLOVER"}


def _load_log(path):
    rows = {}
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("ticket"):
                rows[int(d["ticket"])] = d
    return rows


def _server_offset_hours(mt5):
    """Server clock minus local clock, rounded to the hour.

    Derived from the terminal's own tick time rather than assumed, so this keeps
    working across DST and broker changes.
    """
    info = mt5.symbol_info_tick("EURUSD")
    if not info or not info.time:
        return 0
    delta = datetime.datetime.fromtimestamp(info.time) - datetime.datetime.now()
    return round(delta.total_seconds() / 3600.0)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exec-path", default=DEFAULT_EXEC_PATH, help="execution terminal path")
    ap.add_argument("--log", default=DEFAULT_LOG, help="trade_analytics.jsonl path")
    ap.add_argument("--days", type=int, default=1, help="lookback window in days")
    ap.add_argument("--date", help="restrict to one local date, YYYY-MM-DD")
    args = ap.parse_args()

    try:
        import MetaTrader5 as mt5
    except ImportError:
        raise SystemExit("MetaTrader5 module not installed")

    if not mt5.initialize(path=args.exec_path):
        raise SystemExit("MT5 initialize failed: %s" % (mt5.last_error(),))

    try:
        acc = mt5.account_info()
        off = datetime.timedelta(hours=_server_offset_hours(mt5))
        now = datetime.datetime.now()
        deals = mt5.history_deals_get(now - datetime.timedelta(days=args.days + 2),
                                      now + datetime.timedelta(hours=12))
        if deals is None:
            raise SystemExit("history_deals_get failed: %s" % (mt5.last_error(),))

        pos = collections.defaultdict(
            lambda: {"pnl": 0.0, "comm": 0.0, "swap": 0.0, "sym": "", "vol": 0.0,
                     "in": None, "out": None, "type": None, "reason": None})
        for d in deals:
            p = pos[d.position_id]
            p["sym"] = d.symbol
            p["pnl"] += d.profit
            p["comm"] += d.commission
            p["swap"] += d.swap
            if d.entry == mt5.DEAL_ENTRY_IN:
                p["in"] = d.time
                p["vol"] = d.volume
                p["type"] = "BUY" if d.type == mt5.DEAL_TYPE_BUY else "SELL"
            elif d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY):
                p["out"] = d.time
                p["reason"] = getattr(d, "reason", None)

        want = datetime.date.fromisoformat(args.date) if args.date else None
        cutoff = (now - datetime.timedelta(days=args.days)).date()

        trades = []
        for tkt, v in pos.items():
            if v["in"] is None or v["out"] is None:
                continue
            t_out = datetime.datetime.fromtimestamp(v["out"]) - off
            if want and t_out.date() != want:
                continue
            if not want and t_out.date() < cutoff:
                continue
            trades.append({
                "ticket": tkt, "sym": v["sym"], "type": v["type"], "vol": v["vol"],
                "t_in": datetime.datetime.fromtimestamp(v["in"]) - off, "t_out": t_out,
                "net": v["pnl"] + v["comm"] + v["swap"],
                "reason": REASON.get(v["reason"], str(v["reason"])),
            })
        trades.sort(key=lambda r: r["t_out"])

        logged = _load_log(args.log)

        print("account %s %s | balance %.2f equity %.2f | server offset %+dh"
              % (acc.login, acc.currency, acc.balance, acc.equity,
                 int(off.total_seconds() // 3600)))
        if not trades:
            print("no closed positions in window")
            return

        print("\n%-9s %-8s %-4s %-5s %6s %11s  %-8s %s"
              % ("exit", "symbol", "dir", "lots", "hold", "net " + acc.currency,
                 "closedby", "engine log"))
        missing = []
        for r in trades:
            e = logged.get(r["ticket"])
            if e:
                note = "%s %+.1fp" % (e.get("exit_gate") or "?", float(e.get("pips_profit") or 0))
            else:
                note = "*** NOT LOGGED ***"
                missing.append(r)
            print("%-9s %-8s %-4s %-5.2f %5.1fm %+11.2f  %-8s %s"
                  % (r["t_out"].strftime("%H:%M:%S"), r["sym"], r["type"], r["vol"],
                     (r["t_out"] - r["t_in"]).total_seconds() / 60.0, r["net"],
                     r["reason"], note))

        tot = sum(r["net"] for r in trades)
        w = [r for r in trades if r["net"] > 0]
        print("\nTOTAL %+.2f %s over %d positions, win rate %.1f%% (%d/%d)"
              % (tot, acc.currency, len(trades), 100.0 * len(w) / len(trades), len(w), len(trades)))

        by_reason = collections.defaultdict(lambda: [0, 0.0])
        for r in trades:
            by_reason[r["reason"]][0] += 1
            by_reason[r["reason"]][1] += r["net"]
        print("by close reason:", {k: "n=%d %+.2f" % (v[0], v[1])
                                   for k, v in sorted(by_reason.items(), key=lambda kv: -kv[1][1])})

        by_sym = collections.defaultdict(float)
        for r in trades:
            by_sym[r["sym"]] += r["net"]
        print("by symbol:", {k: round(v, 2) for k, v in sorted(by_sym.items(), key=lambda kv: -kv[1])})

        if missing:
            m_tot = sum(r["net"] for r in missing)
            print("\nMISSING FROM %s: %d of %d positions, %+.2f %s (%.1f%% of total P&L)"
                  % (args.log, len(missing), len(trades), m_tot, acc.currency,
                     100.0 * m_tot / tot if tot else 0.0))
            print("close reasons of the missing:",
                  dict(collections.Counter(r["reason"] for r in missing)))
            print("Any TP/SL here means the daemon predates the broker-close logging fix;")
            print("restart it so record_exit fires for broker closes too.")
        else:
            print("\nall %d positions present in %s" % (len(trades), args.log))
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
