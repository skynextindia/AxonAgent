"""Start the dashboard with MT5 bridge client connected to Windows bridge."""
import sys
import time
import logging

sys.path.insert(0, '.')

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

from axonai.realtime.api_server import start_dashboard, get_dashboard
from axonai.realtime.mt5_bridge_client import BridgeClient
from axonai.realtime.tick_behavior import TickBehaviorAnalyzer

# Start dashboard on localhost:8000
server = start_dashboard(host='127.0.0.1', port=8000)
print('Dashboard started on http://127.0.0.1:8000', flush=True)

# Determine Windows host IP from WSL default gateway
import os
import platform

bridge_host = os.environ.get("BRIDGE_HOST")
if not bridge_host:
    if platform.system() == "Windows":
        bridge_host = "127.0.0.1"
    else:
        import subprocess
        try:
            result = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True, text=True, timeout=5
            )
            bridge_host = result.stdout.strip().split()[2]
        except Exception:
            bridge_host = "127.0.0.1"  # fallback

print(f'Bridge host: {bridge_host}', flush=True)

# ── Tick Behavior Analyzer ──────────────────────────────────────────
analyzer = TickBehaviorAnalyzer(
    log_dir="reports/tick_behavior",
    snapshot_interval=5,          # snapshot every 5 ticks
    max_log_files=20,
)
logger = logging.getLogger(__name__)
logger.info("TickBehaviorAnalyzer started (snapshot every 5 ticks → reports/tick_behavior/)")


def on_tick_from_bridge(data: dict):
    """Bridge tick callback → feed into analyzer and broadcast enriched data."""
    bid = data.get("bid")
    ask = data.get("ask")
    time_s = data.get("time")
    if bid is not None and ask is not None:
        analyzer.feed_tick(bid=bid, ask=ask, time_s=time_s)
        
        # Enrich raw tick with calculated high-fidelity stats
        if hasattr(analyzer, "last_state") and analyzer.last_state:
            state = analyzer.last_state
            data["tick_velocity"] = round(state.velocity, 2)
            data["tick_imbalance_10s"] = round(state.imbalance_10s, 2)
            data["tick_imbalance_60s"] = round(state.imbalance_60s, 2)
            data["tick_imbalance_300s"] = round(state.imbalance_300s, 2)
            data["tick_spread_delta"] = round(state.spread_delta, 5)
            data["tick_collapse"] = bool(state.velocity_collapse)
            data["tick_agg_shift"] = bool(state.aggression_shift)
            data["tick_absorption"] = bool(state.absorption)

        # Broadcast the enriched tick data to all connected clients!
        server.broadcast(data)


def request_historical_candles(client):
    """Request M15, H1 and H4 historical candles from the bridge."""
    symbol = "EURUSD"
    now = int(time.time())
    logger = logging.getLogger(__name__)

    # M15: last 3 days (fits initial view nicely)
    from_m15 = now - (3 * 86400)
    ok = client.request_historical(symbol, "M15", from_m15, now)
    logger.info("Requested historical M15: %s", "OK" if ok else "FAILED")

    # H1 / H4: last 7 days
    from_hl = now - (7 * 86400)
    for tf in ["H1", "H4"]:
        ok = client.request_historical(symbol, tf, from_hl, now)
        logger.info(f"Requested historical %s: {'OK' if ok else 'FAILED'}", tf)


# Create bridge client and wire it to the dashboard
client = BridgeClient(
    host=bridge_host,
    port=8765,
    dashboard_server=server,
    auto_reconnect=True,
    reconnect_delay=3.0,
    on_connected=request_historical_candles,
    on_tick=on_tick_from_bridge,
)
client.start()
print(f'BridgeClient started, connecting to ws://{bridge_host}:8765', flush=True)

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print('\nShutting down...', flush=True)
    client.stop()
