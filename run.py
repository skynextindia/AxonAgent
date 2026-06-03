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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler()]
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
    parser.add_argument("--symbol", type=str, default="EURUSDm",
                        help="Symbol to trade")
    args = parser.parse_args()

    env = "wsl" if is_wsl() else "windows" if is_windows() else "linux"
    bridge_mode = args.bridge or (env == "wsl" and not args.direct)

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
        print(f"  Symbol: {args.symbol}")
        print()

        # Import and start dashboard
        from axonai.realtime.api_server import start_dashboard
        server = start_dashboard(host=args.host, port=args.port)

        # Import and start daemon
        from axonai.realtime.daemon import AxonDaemon
        from axonai.default_config import DEFAULT_CONFIG
        config = DEFAULT_CONFIG.copy()
        config["symbol"] = args.symbol
        config["realtime_dry_run"] = True
        daemon = AxonDaemon(symbol=args.symbol, config=config)
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
