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


# ── Execution bridge (dual-terminal: feed=Exness, exec=MetaQuotes) ──

def start_execution_bridge(exec_path, port):
    """Launch windows/execution_bridge.py bound to the execution terminal.

    The MetaTrader5 package binds one terminal per process, so order routing
    to a *second* terminal (MetaQuotes) must run in its own process. Returns
    the Popen handle, or None if it never reported healthy.
    """
    import subprocess
    import urllib.request

    bridge_script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "windows", "execution_bridge.py"
    )
    if not os.path.exists(bridge_script):
        print(f"  [!] Execution bridge script not found: {bridge_script}")
        return None

    cmd = [sys.executable, bridge_script, "--port", str(port), "--host", "127.0.0.1"]
    if exec_path:
        cmd += ["--path", exec_path]
    print(f"  [+] Starting execution bridge (MetaQuotes) on port {port}")
    print(f"      exec terminal: {exec_path or 'auto-detect'}")
    proc = subprocess.Popen(cmd)

    # Poll the bridge health endpoint (port + 1) until it answers or we give up.
    health_url = f"http://127.0.0.1:{port + 1}/health"
    import time
    for _ in range(30):  # ~15s
        if proc.poll() is not None:
            print(f"  [!] Execution bridge exited early (code {proc.returncode}). "
                  f"Orders will FAIL until it is running.")
            return None
        try:
            with urllib.request.urlopen(health_url, timeout=1) as resp:
                if resp.status == 200:
                    print("  [+] Execution bridge is healthy and connected to MT5.")
                    return proc
        except Exception:
            pass
        time.sleep(0.5)

    print("  [!] Execution bridge did not report healthy within 15s. "
          "Continuing — check the bridge window for MT5 login errors.")
    return proc


# ── Main ───────────────────────────────────────────────────────────

def main():
    import logging
    from logging.handlers import RotatingFileHandler

    # Persist logs to disk so a finished/closed session can still be diagnosed
    # (console-only logging left no trail to explain missing order execution).
    log_dir = os.path.join(os.path.expanduser("~"), ".axonai", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "axon.log")
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(fmt))
    logging.basicConfig(
        level=logging.DEBUG,
        format=fmt,
        handlers=[logging.StreamHandler(), file_handler],
    )
    logging.getLogger(__name__).info("Logging to %s", log_file)
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
    parser.add_argument("--paper", action="store_true",
                        help="Paper-trade mode: simulate fills internally, never send orders to MT5. "
                             "Safe for testing; dry-run (default) still places real demo orders.")
    parser.add_argument("--live", action="store_true",
                        help="Live execution mode: disables dry-run (fixed 1.00 lot size) and enables dynamic position sizing.")
    parser.add_argument("--feed-path", type=str, default=None,
                        help="Path to feed MT5 terminal64.exe (e.g. Exness)")
    parser.add_argument("--exec-path", type=str, default=None,
                        help="Path to execution MT5 terminal64.exe (e.g. MetaQuotes)")
    args = parser.parse_args()

    if args.feed_path:
        from axonai.dataflows.mt5_data import set_feed_terminal_path
        set_feed_terminal_path(args.feed_path)

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
        config["realtime_dry_run"] = not args.live
        # Paper-trade: simulate fills (no MT5 send). Default dry-run still sends real demo orders.
        config["paper_trade"] = args.paper
        if args.feed_path:
            config["mt5_terminal_path"] = args.feed_path

        # Dual-terminal: data feed stays on this process (Exness via --feed-path),
        # while orders route to a SEPARATE execution bridge process bound to the
        # MetaQuotes terminal (--exec-path). One MT5 connection per process, so
        # execution must live in its own process.
        bridge_proc = None
        if args.exec_path:
            if not args.feed_path:
                print("  [!] --exec-path set without --feed-path: the data feed will "
                      "auto-detect a terminal and may bind to MetaQuotes. Pass --feed-path "
                      "(Exness) to keep feed and execution on separate terminals.")
            bridge_port = 8766
            bridge_proc = start_execution_bridge(args.exec_path, bridge_port)
            config["realtime_execution_mode"] = "bridge"
            config["realtime_execution_bridge_host"] = "127.0.0.1"
            config["realtime_execution_bridge_port"] = bridge_port

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
            if bridge_proc is not None and bridge_proc.poll() is None:
                print("  Stopping execution bridge...")
                bridge_proc.terminate()
                try:
                    bridge_proc.wait(timeout=5)
                except Exception:
                    bridge_proc.kill()


if __name__ == "__main__":
    main()
