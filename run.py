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


# ── Main ───────────────────────────────────────────────────────────

def main():
    import logging
    from logging.handlers import RotatingFileHandler
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "daemon.log"),
        maxBytes=10 * 1024 * 1024,   # 10 MB per file
        backupCount=10,              # keep 10 rolls → ~100 MB cap
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), file_handler],
    )
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
    args = parser.parse_args()

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
        if args.exec_node:
            config["exec_node_mode"] = True
        if args.mirror_url:
            config["mirror_enabled"] = True
            config["mirror_url"] = args.mirror_url
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
                ExecNodeServer(supervisor.daemons, port=args.exec_port).start()
            supervisor.start()  # blocks until stopped, then tears down MT5
        else:
            daemon = AxonDaemon(symbol=symbols[0], config=config)
            # Lead, single-pair: attach a mirror client (multi-pair uses the
            # supervisor's shared one instead).
            if args.mirror_url and not args.exec_node:
                from axonai.realtime.mirror_client import MirrorClient
                daemon.mirror_client = MirrorClient(config["mirror_url"])
                daemon.mirror_client.start()
            # Execution node: expose the inbound server BEFORE the blocking start.
            if args.exec_node:
                from axonai.realtime.exec_node import ExecNodeServer
                ExecNodeServer({symbols[0]: daemon}, port=args.exec_port).start()
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
