#!/usr/bin/env python3
"""
Unified launcher for AxonAI Dashboard.

Auto-detects environment:
  Windows → starts daemon + dashboard (direct MT5)
  WSL     → connects to MT5 bridge on Windows host + dashboard

Usage:
    python run.py                          # auto-detect
    python run.py --bridge                 # force bridge mode
    python run.py --bridge-host 172.x.x.x  # specify bridge host
    python run.py --direct                 # force direct mode
"""

import sys
import os
import platform
import argparse

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Detect environment ─────────────────────────────────────────────

def is_wsl():
    """Check if running inside WSL."""
    return "microsoft" in platform.uname().release.lower()


def is_windows():
    """Check if running on native Windows."""
    return sys.platform == "win32" or sys.platform == "cygwin"


def get_windows_host_ip():
    """Get the Windows host IP from WSL."""
    import subprocess
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5
        )
        parts = result.stdout.split()
        for i, p in enumerate(parts):
            if p == "via" and i + 1 < len(parts):
                return parts[i + 1]
    except Exception:
        pass
    return "127.0.0.1"


def _single_pair_snapshot(daemon):
    """Mirror reconcile snapshot for the single-pair lead path.

    Multi-pair goes through ``DaemonSupervisor._mirror_snapshot``; this is the
    one-daemon equivalent. A pair whose position state cannot be read must land
    in ``unknown``, never be merely omitted — absence from ``open`` means "the
    lead is flat" and authorises the node to close its position.
    """
    from axonai.default_config import _canonical_symbol
    canon = _canonical_symbol(getattr(daemon, "mt5_symbol", ""))
    try:
        state = daemon.mirror_position_state()
    except Exception:
        return {"open": {}, "unknown": [canon]}
    if not state.get("ok"):
        return {"open": {}, "unknown": [canon]}
    if state.get("signal"):
        return {"open": {canon: {"signal": state["signal"]}}, "unknown": []}
    return {"open": {}, "unknown": []}


# ── Main ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AxonAI Dashboard")
    parser.add_argument("--bridge", action="store_true", help="Force bridge mode (WSL)")
    parser.add_argument("--direct", action="store_true", help="Force direct MT5 mode (Windows)")
    parser.add_argument("--bridge-host", type=str, default=None,
                        help="MT5 bridge host (default: auto-detect)")
    parser.add_argument("--bridge-port", type=int, default=8765,
                        help="MT5 bridge port")
    parser.add_argument("--host", type=str, default="127.0.0.1",
                        help="Dashboard host")
    parser.add_argument("--port", type=int, default=8000,
                        help="Dashboard port")
    parser.add_argument("--symbol", type=str, default="EURUSD",
                        help="Symbol to trade")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated pairs for multi-pair mode, e.g. "
                             "EURUSD,USDJPY (overrides --symbol; one daemon per pair)")
    parser.add_argument("--mt5-path", type=str, default=None,
                        help="Path to MT5 terminal executable")
    parser.add_argument("--login", type=int, default=None,
                        help="MT5 login account")
    parser.add_argument("--password", type=str, default=None,
                        help="MT5 password")
    parser.add_argument("--server", type=str, default=None,
                        help="MT5 server")
    parser.add_argument("--max-daily-drawdown-pct", type=float, default=None,
                        help="Maximum daily drawdown percent limit (e.g. 5.0 for FTMO)")
    parser.add_argument("--max-daily-loss-amount", type=float, default=None,
                        help="Maximum daily loss amount limit (e.g. 5000.0 for FTMO)")
    parser.add_argument("--exec-node", action="store_true",
                        help="Run as an execution node: self-detection OFF; receives entry/close "
                             "decisions from a lead brain and executes them with the engine's own "
                             "order management, sized to THIS account.")
    parser.add_argument("--exec-port", type=int, default=8770,
                        help="Execution-node inbound signal WebSocket port (with --exec-node)")
    parser.add_argument("--mirror-url", type=str, default=None,
                        help="Lead brain: forward each entry/close decision to this execution "
                             "node URL, e.g. ws://127.0.0.1:8770")
    parser.add_argument("--mt5-symbol-suffix", type=str, default=None,
                        help="Broker symbol suffix override (e.g. .i for Eightcap)")
    parser.add_argument("--prop-firm", action="store_true",
                        help="Enable the prop-firm compliance guard for THIS account only: "
                             "overall drawdown floor + server-day daily loss limit, and "
                             "flatten-on-breach. Applies solely to this process/account.")
    parser.add_argument("--prop-max-drawdown-pct", type=float, default=None,
                        help="Prop overall drawdown %% from initial balance (default 10.0)")
    parser.add_argument("--prop-daily-loss-pct", type=float, default=None,
                        help="Prop daily loss limit %% (default 5.0)")
    parser.add_argument("--prop-trailing-drawdown", action="store_true",
                        help="Overall drawdown floor trails equity highs (e.g. Zero accounts) "
                             "instead of being static from the initial balance")
    parser.add_argument("--prop-initial-balance", type=float, default=None,
                        help="Prop starting balance baseline (default: seed from the account)")
    parser.add_argument("--skip-falling-knife", action="store_true",
                        help="Enable the falling-knife entry filter (skip a BUY whose M15 trigger "
                             "candle closed below its open). Validated net-negative quadrant; "
                             "default OFF in code, opt-in per launch. Lead-only (no-op on exec-node).")
    parser.add_argument("--trail-dist", type=float, default=None,
                        help="Override the trailing-stop distance (× ATR) for all pairs, e.g. 1.0. "
                             "Default (unset) keeps the validated 0.35. Widening lifts capture; "
                             "applies to lead AND exec-node. Soak on demo first.")
    parser.add_argument("--risk-cap-per-trade", type=float, default=None,
                        help="Cap any single trade's stop-risk at this %% of equity (e.g. 1.5); "
                             "the lot is trimmed to fit. Prop-account risk rule.")
    parser.add_argument("--risk-cap-combined", type=float, default=None,
                        help="Cap TOTAL open stop-risk at this %% of equity (e.g. 2.5); a new entry "
                             "is shrunk to the remaining budget, or blocked if min-lot won't fit.")
    parser.add_argument("--enforce-max-stop", action="store_true",
                        help="Hard-cap SL/TP at the per-pair max_stop_pips (USDJPY 10, EURUSD 16) so "
                             "the stop never widens past the cap regardless of ATR.")
    parser.add_argument("--hard-distance", action="store_true",
                        help="Hard-distance mode: SL = TP = trailing distance = the per-pair "
                             "hard_stop_pips (EURUSD 20, USDJPY 30), session-independent, ignoring "
                             "ATR / min-stop / vol floors / enforce-max-stop. Applies to lead AND node.")
    parser.add_argument("--fixed-lot", type=float, default=None,
                        help="Size EVERY entry to exactly this lot, bypassing risk-% sizing, the "
                             "correlation size-scale, and lot mirroring (e.g. lead runs 1.0).")
    parser.add_argument("--max-loss-usd", type=float, default=None,
                        help="Derive each entry's lot so a full stop-out loses at most this many USD "
                             "(e.g. node runs 1800). Overrides risk-% sizing; the combined risk cap "
                             "can still trim it further.")
    parser.add_argument("--node-lot-multiple", type=float, default=None,
                        help="Exec-node only: size each routed entry to this multiple of the LEAD's "
                             "executed lot (e.g. 10 → node trades 10× the Eightcap lot).")
    parser.add_argument("--risk-pct", type=float, default=None,
                        help="Per-trade risk as %% of equity (e.g. 1.9). Overrides the per-pair "
                             "risk_pct so every entry sizes to risk this share at its stop.")
    parser.add_argument("--skip-panic-buy", action="store_true",
                        help="Veto BUY entries in panic regime (OOS-validated worst BUY pocket).")
    parser.add_argument("--skip-session-buy", action="store_true",
                        help="Veto BUY entries inside the active-session window "
                             "(default 08-16 UTC, the OOS-validated worst BUY block).")
    parser.add_argument("--session-buy-window", type=str, default=None,
                        help="Override the --skip-session-buy window as START-END in "
                             "UTC hours, e.g. 8-16.")
    parser.add_argument("--skip-all-buy", action="store_true",
                        help="Suppress ALL BUY entries (full directional short-only bet).")
    args = parser.parse_args()

    # ── logging: PER-INSTANCE log file ────────────────────────────────────────
    # Configured AFTER arg parsing because the filename depends on the role. The
    # lead and the execution node run from the SAME working directory, so a
    # single "daemon.log" meant two RotatingFileHandlers on one path: on Windows
    # the 10 MB rollover then fails with a sharing violation (the peer holds an
    # open handle without FILE_SHARE_DELETE), logging swallows it via
    # handleError, and the file grows past its cap forever. Worse, the two
    # accounts' records interleave with nothing to tell them apart. One file per
    # role fixes both; %(process)d makes any future merge attributable.
    instance_tag = "_node" if args.exec_node else ""
    # Exported so the dashboard can pick it up in its constructor: _load_session()
    # runs before any daemon (and therefore any config) is registered.
    os.environ["AXONAI_INSTANCE_TAG"] = instance_tag
    import logging
    from logging.handlers import RotatingFileHandler
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    os.makedirs(log_dir, exist_ok=True)
    log_fmt = "%(asctime)s [%(levelname)s] pid%(process)d %(name)s: %(message)s"
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, f"daemon{instance_tag}.log"),
        maxBytes=10 * 1024 * 1024,   # 10 MB per file
        backupCount=10,              # keep 10 rolls → ~100 MB cap
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(log_fmt))
    logging.basicConfig(
        level=logging.INFO,
        format=log_fmt,
        handlers=[logging.StreamHandler(), file_handler],
    )

    # ── singleton guard: refuse to start if another instance already owns a port
    # this process needs. Catches accidental double-launches regardless of shell,
    # PATH-python vs .venv-python, or which .bat/IDE fired the second one.
    #
    # The exec-node is NOT exempt: it serves a dashboard on args.port AND binds
    # the mirror server on args.exec_port, so two exec-nodes racing for those
    # ports is exactly how a system-python node and a .venv node both came up and
    # split the dashboards. Guard both ports so the second launch cleanly refuses
    # instead of winning a non-deterministic race.
    import socket
    guard_ports = [(args.host, args.port, "Dashboard")]
    if args.exec_node:
        guard_ports.append((args.host, args.exec_port, "Mirror-server"))
    for _host, _port, _what in guard_ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((_host, _port))
        except OSError as e:
            print("=" * 60)
            print(f"  ANOTHER AXONAI INSTANCE IS ALREADY RUNNING")
            print(f"  {_what} port {_host}:{_port} is bound ({e}).")
            print(f"  Refusing to start a duplicate — trading the same account")
            print(f"  from two processes is dangerous. Stop the other first")
            print(f"  (run_system.bat option 7 kills all AxonAI processes).")
            print("=" * 60)
            sys.exit(2)
        finally:
            s.close()

    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else [args.symbol]
    symbols = [s for s in symbols if s]

    env = "wsl" if is_wsl() else "windows" if is_windows() else "linux"
    bridge_mode = args.bridge or (env == "wsl" and not args.direct)

    if is_windows():
        try:
            import ctypes
            flags = 0x80000000 | 0x00000001 | 0x00000040
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
            print("  [+] Windows Sleep Prevention activated (ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED)")
        except Exception as e:
            print(f"  [!] Failed to activate sleep prevention: {e}")

    print("=" * 60)
    print(f"  AxonAI Dashboard — Environment: {env.upper()}")
    print(f"  Mode: {'Bridge (MT5 via Windows)' if bridge_mode else 'Direct (MT5 local)'}")
    print("=" * 60)
    print()

    if bridge_mode:
        # WSL mode: connect to MT5 bridge on Windows
        bridge_host = args.bridge_host or get_windows_host_ip()
        print(f"  Connecting to MT5 bridge at {bridge_host}:{args.bridge_port}")
        print(f"  Dashboard: http://{args.host}:{args.port}")
        print()

        # Start dashboard server
        from axonai.realtime.api_server import start_dashboard
        server = start_dashboard(host=args.host, port=args.port)

        # Start bridge client
        from axonai.realtime.mt5_bridge_client import BridgeClient
        client = BridgeClient(
            host=bridge_host,
            port=args.bridge_port,
            dashboard_server=server,
            auto_reconnect=True,
        )
        client.start()

        print("  Dashboard running. Press Ctrl+C to stop.")
        try:
            import time
            while True:
                time.sleep(1)
                if not client.is_connected():
                    print(f"  [~] Waiting for bridge connection to {bridge_host}:{args.bridge_port}...")
        except KeyboardInterrupt:
            print("\n  Stopping...")
            client.stop()

    else:
        # Windows / Direct mode: start daemon + dashboard
        print(f"  Dashboard: http://{args.host}:{args.port}")
        print(f"  Symbol(s): {', '.join(symbols)}")
        print()

        # Import and start dashboard
        from axonai.realtime.api_server import start_dashboard
        server = start_dashboard(host=args.host, port=args.port)

        # Import and start daemon
        from axonai.realtime.daemon import AxonDaemon
        from axonai.default_config import DEFAULT_CONFIG
        config = DEFAULT_CONFIG.copy()
        config["symbol"] = symbols[0]
        config["realtime_dry_run"] = True
        
        # Override config settings with CLI arguments if provided
        if args.mt5_path:
            config["mt5_terminal_path"] = args.mt5_path
        if args.login:
            config["mt5_login"] = args.login
        if args.password:
            config["mt5_password"] = args.password
        if args.server:
            config["mt5_server"] = args.server
        if args.max_daily_drawdown_pct is not None:
            config["risk_max_daily_drawdown_pct"] = args.max_daily_drawdown_pct
        if args.max_daily_loss_amount is not None:
            config["risk_max_daily_loss_amount"] = args.max_daily_loss_amount
        if args.mt5_symbol_suffix is not None:
            config["mt5_symbol_suffix"] = args.mt5_symbol_suffix
        # Tag every per-instance report file so the two processes never share a
        # writer. Empty for the lead, which keeps the historical untagged names
        # (reports/signals.jsonl etc.) so its dashboard history stays continuous.
        config["instance_tag"] = instance_tag
        if args.exec_node:
            config["exec_node_mode"] = True
        if args.mirror_url:
            config["mirror_enabled"] = True
            config["mirror_url"] = args.mirror_url
        if args.skip_falling_knife:
            config["entry_skip_falling_knife"] = True
        if args.trail_dist is not None:
            config["trail_dist_atr_mult_override"] = args.trail_dist
        if args.risk_cap_per_trade is not None:
            config["risk_cap_per_trade_pct"] = args.risk_cap_per_trade
        if args.risk_cap_combined is not None:
            config["risk_cap_combined_pct"] = args.risk_cap_combined
        if args.enforce_max_stop:
            config["enforce_max_stop_pips"] = True
        if args.hard_distance:
            config["hard_distance_mode"] = True
        if args.fixed_lot is not None:
            config["fixed_lot"] = args.fixed_lot
        if args.max_loss_usd is not None:
            config["max_loss_per_trade_usd"] = args.max_loss_usd
        if args.node_lot_multiple is not None:
            config["exec_node_lot_multiple"] = args.node_lot_multiple
        if args.risk_pct is not None:
            config["realtime_risk_pct_override"] = args.risk_pct / 100.0
        if args.skip_panic_buy:
            config["entry_skip_panic_buy"] = True
        if args.skip_session_buy:
            config["entry_skip_session_buy"] = True
        if args.session_buy_window:
            _s, _, _e = args.session_buy_window.partition("-")
            config["entry_skip_session_buy_start"] = int(_s)
            config["entry_skip_session_buy_end"] = int(_e)
        if args.skip_all_buy:
            config["entry_skip_all_buy"] = True
        # Prop-firm guard: scoped to THIS process/account only.
        if args.prop_firm:
            config["prop_guard_enabled"] = True
        if args.prop_max_drawdown_pct is not None:
            config["prop_max_drawdown_pct"] = args.prop_max_drawdown_pct
        if args.prop_daily_loss_pct is not None:
            config["prop_daily_loss_pct"] = args.prop_daily_loss_pct
        if args.prop_trailing_drawdown:
            config["prop_max_drawdown_trailing"] = True
        if args.prop_initial_balance is not None:
            config["prop_initial_balance"] = args.prop_initial_balance

        # Set environment variables from final config so parameterless mt5_initialize() gets them
        if config.get("mt5_terminal_path"):
            os.environ["AXONAI_MT5_TERMINAL_PATH"] = str(config["mt5_terminal_path"])
        if config.get("mt5_login"):
            os.environ["AXONAI_MT5_LOGIN"] = str(config["mt5_login"])
        if config.get("mt5_password"):
            os.environ["AXONAI_MT5_PASSWORD"] = str(config["mt5_password"])
        if config.get("mt5_server"):
            os.environ["AXONAI_MT5_SERVER"] = str(config["mt5_server"])
        if config.get("risk_max_daily_drawdown_pct") is not None:
            os.environ["AXONAI_RISK_MAX_DAILY_DRAWDOWN_PCT"] = str(config["risk_max_daily_drawdown_pct"])
        if config.get("risk_max_daily_loss_amount") is not None:
            os.environ["AXONAI_RISK_MAX_DAILY_LOSS_AMOUNT"] = str(config["risk_max_daily_loss_amount"])

        if args.exec_node:
            print(f"  EXECUTION-NODE mode (self-detection OFF) — inbound signals on ws://127.0.0.1:{args.exec_port}")
        if args.mirror_url:
            print(f"  LEAD order mirror ENABLED → forwarding decisions to {args.mirror_url}")
        if args.prop_firm:
            print(f"  PROP-FIRM GUARD ON (this account only): "
                  f"max DD {config['prop_max_drawdown_pct']}% "
                  f"({'trailing' if config.get('prop_max_drawdown_trailing') else 'static'}), "
                  f"daily {config['prop_daily_loss_pct']}%, "
                  f"buffer {config['prop_safety_buffer_pct']}%, flatten-on-breach")

        if len(symbols) > 1:
            # Multi-pair: one daemon thread per pair over a shared MT5 connection.
            from axonai.realtime.supervisor import DaemonSupervisor
            print(f"  Multi-pair mode: {', '.join(symbols)}")
            print("  Dashboard + Daemons running. Press Ctrl+C to stop.")
            supervisor = DaemonSupervisor(symbols, config)
            if args.exec_node:
                # Execution node: expose an inbound server that routes forwarded
                # decisions to the per-pair daemons the supervisor just built.
                from axonai.realtime.exec_node import ExecNodeServer
                ExecNodeServer(supervisor.daemons, port=args.exec_port, config=config).start()
            supervisor.start()  # blocks until stopped, then tears down MT5
        else:
            daemon = AxonDaemon(symbol=symbols[0], config=config)
            # Lead, single-pair: attach a mirror client (multi-pair uses the
            # supervisor's shared one instead).
            if args.mirror_url and not args.exec_node:
                from axonai.realtime.mirror_client import MirrorClient
                d = daemon  # snapshot provider closes over the single daemon
                daemon.mirror_client = MirrorClient(
                    config["mirror_url"],
                    queue_max=int(config.get("mirror_queue_max", 200) or 200),
                    entry_ttl=float(config.get("mirror_entry_ttl_seconds", 45.0) or 45.0),
                    snapshot_provider=lambda: _single_pair_snapshot(d),
                )
                daemon.mirror_client.start()
            # Execution node: expose the inbound server BEFORE the blocking start.
            if args.exec_node:
                from axonai.realtime.exec_node import ExecNodeServer
                ExecNodeServer({symbols[0]: daemon}, port=args.exec_port, config=config).start()
            daemon.start()

            print("  Dashboard + Daemon running. Press Ctrl+C to stop.")
            try:
                import time
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n  Stopping...")
                daemon.stop()


if __name__ == "__main__":
    main()
