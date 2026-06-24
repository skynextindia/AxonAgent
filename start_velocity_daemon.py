#!/usr/bin/env python3
"""Start AxonAI Velocity Intelligence Daemon for Live Trading

This script launches the velocity intelligence trading system in live mode
on your connected MT5 terminal (demo or live account).

Usage:
    python start_velocity_daemon.py --mode demo --symbol EURUSD

Features:
- Pre-entry velocity baseline tracking
- Entry qualification on impulse strength (z-score > 2.0)
- Real-time trade health monitoring
- Adaptive exit based on velocity health + reversal factors
- Live dashboard streaming
"""

import logging
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("velocity_daemon.log"),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("velocity_daemon")

# Parse arguments
parser = argparse.ArgumentParser(description="AxonAI Velocity Intelligence Daemon")
parser.add_argument("--mode", type=str, default="demo", choices=["demo", "live", "paper"],
                   help="Trading mode: demo (default), live, or paper")
parser.add_argument("--symbol", type=str, default="EURUSD", help="Trading pair (default: EURUSD)")
parser.add_argument("--account-type", type=str, default="hedging", choices=["hedging", "netting"],
                   help="MT5 account type")
parser.add_argument("--magic-number", type=int, default=123456, help="Magic number for orders")
parser.add_argument("--lot-size", type=float, default=0.01, help="Default lot size")
parser.add_argument("--cooldown", type=int, default=300, help="Entry cooldown (seconds)")
args = parser.parse_args()

logger.info("="*70)
logger.info("🚀 AxonAI VELOCITY INTELLIGENCE DAEMON")
logger.info("="*70)
logger.info(f"📊 Symbol: {args.symbol}")
logger.info(f"🎯 Mode: {args.mode.upper()}")
logger.info(f"💰 Lot Size: {args.lot_size}")
logger.info(f"⏱️  Cooldown: {args.cooldown}s")
logger.info(f"✨ Magic Number: {args.magic_number}")

# ───────────────────────────────────────────────────────────────────────────
# LOAD CONFIGURATION WITH VELOCITY INTELLIGENCE
# ───────────────────────────────────────────────────────────────────────────
from axonai.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config.update({
    "realtime_magic_number": args.magic_number,
    "realtime_default_lot_size": args.lot_size,
    "realtime_cooldown_seconds": args.cooldown,
    "paper_trade": (args.mode == "paper"),
    "backtest_mode": False,

    # VELOCITY INTELLIGENCE SYSTEM CONFIGURATION
    "realtime_entry_zscore_threshold": 2.0,
    "realtime_velocity_health_threshold_exit": 0.40,
    "realtime_velocity_health_threshold_trail": 0.70,
    "realtime_reversal_risk_threshold": 0.70,
    "realtime_velocity_window_size": 30,
    "realtime_pre_entry_baseline_window": 100,
    "realtime_velocity_min_profit_tight_trail": 0.25,
})

logger.info("\n✅ Configuration loaded with Velocity Intelligence")
logger.info("  - Entry z-score threshold: 2.0σ")
logger.info("  - Health exit threshold: 0.40")
logger.info("  - Health trail threshold: 0.70")
logger.info("  - Reversal risk threshold: 0.70")

# ───────────────────────────────────────────────────────────────────────────
# INITIALIZE MT5 CONNECTION
# ───────────────────────────────────────────────────────────────────────────
try:
    import MetaTrader5 as mt5

    if not mt5.initialize():
        logger.error("❌ Failed to initialize MT5")
        sys.exit(1)

    logger.info("\n✅ MT5 Connection Established")

    # Get account info
    acc_info = mt5.account_info()
    if acc_info:
        logger.info(f"  💳 Account: {acc_info.name}")
        logger.info(f"  💰 Balance: ${acc_info.balance:,.2f}")
        logger.info(f"  📈 Equity: ${acc_info.equity:,.2f}")
        logger.info(f"  📊 Leverage: 1:{acc_info.leverage}")

    # Select symbol
    symbol = f"{args.symbol}" if "=" not in args.symbol else args.symbol
    if not mt5.symbol_select(symbol, True):
        logger.error(f"❌ Failed to select symbol {symbol}")
        sys.exit(1)

    logger.info(f"  ✓ Symbol selected: {symbol}")

except ImportError:
    logger.warning("⚠️  MetaTrader5 module not found. Running in simulation mode.")
    logger.warning("    Connect to MT5 terminal manually before starting daemon.")
    mt5 = None
except Exception as e:
    logger.error(f"❌ MT5 initialization error: {e}")
    sys.exit(1)

# ───────────────────────────────────────────────────────────────────────────
# INITIALIZE DAEMON
# ───────────────────────────────────────────────────────────────────────────
logger.info("\n🔧 Initializing AxonAI Daemon...")

from axonai.realtime.daemon import AxonDaemon

try:
    daemon = AxonDaemon(
        symbol=args.symbol,
        config=config
    )
    logger.info("✅ Daemon initialized successfully")
except Exception as e:
    logger.error(f"❌ Daemon initialization failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ───────────────────────────────────────────────────────────────────────────
# START DAEMON
# ───────────────────────────────────────────────────────────────────────────
logger.info("\n" + "="*70)
logger.info("▶️  STARTING VELOCITY INTELLIGENCE TRADING DAEMON")
logger.info("="*70)
logger.info("\n📊 LIVE DASHBOARD: http://127.0.0.1:8000/")
logger.info("   (Open in browser to monitor trades in real-time)")
logger.info("\n🛑 Press Ctrl+C to stop daemon gracefully\n")

try:
    daemon.start()
except KeyboardInterrupt:
    logger.info("\n\n⏹️  STOPPING DAEMON...")
    daemon.shutdown()
    logger.info("✅ Daemon stopped cleanly")
except Exception as e:
    logger.error(f"\n❌ DAEMON ERROR: {e}")
    import traceback
    traceback.print_exc()
    daemon.shutdown()
    sys.exit(1)

if mt5:
    mt5.shutdown()
    logger.info("✅ MT5 connection closed")

logger.info("="*70)
logger.info("DAEMON SESSION ENDED")
logger.info("="*70)
