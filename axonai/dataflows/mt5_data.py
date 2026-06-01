"""MetaTrader 5 live data provider.

Connects to a running MT5 terminal to fetch real-time ticks, multi-timeframe
OHLCV bars, and compute technical indicators. Falls back gracefully when MT5
is not available.

Symbol mapping: yfinance ``EURUSD=X`` → MT5 ``EURUSDm`` (configurable suffix).
"""

from __future__ import annotations

import atexit
import logging
from datetime import datetime, timedelta
from typing import Annotated, Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# Lazy import so the module loads even when MetaTrader5 isn't installed
_mt5 = None
_initialized = False

# ── MT5 timeframe constants ──────────────────────────────────────────────
# Mapping from human-readable labels to MT5 TIMEFRAME_* constants.
# Populated on first successful mt5_initialize().
_TF_MAP: Dict[str, int] = {}
_cached_tz_offset: Optional[int] = None


def _load_mt5():
    """Lazy-import the MetaTrader5 package."""
    global _mt5
    if _mt5 is None:
        try:
            import MetaTrader5 as mt5
            _mt5 = mt5
        except ImportError:
            raise ImportError(
                "MetaTrader5 package not installed. Run: pip install MetaTrader5"
            )
    return _mt5


def mt5_initialize(terminal_path: Optional[str] = None) -> bool:
    """Connect to the MT5 terminal. Cached — safe to call repeatedly."""
    global _initialized, _TF_MAP
    if _initialized:
        return True
    mt5 = _load_mt5()

    kwargs = {}
    if terminal_path:
        kwargs["path"] = terminal_path

    if not mt5.initialize(**kwargs):
        err = mt5.last_error()
        logger.warning("MT5 initialize failed: %s", err)
        return False

    _initialized = True
    atexit.register(mt5_shutdown)

    # Build timeframe map using actual MT5 constants
    _TF_MAP.update({
        "M1":  mt5.TIMEFRAME_M1,
        "M5":  mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1":  mt5.TIMEFRAME_H1,
        "H4":  mt5.TIMEFRAME_H4,
        "D1":  mt5.TIMEFRAME_D1,
        "W1":  mt5.TIMEFRAME_W1,
        "MN1": mt5.TIMEFRAME_MN1,
    })
    logger.info("MT5 connected: %s", mt5.terminal_info())
    return True


def mt5_shutdown():
    """Disconnect from MT5 terminal."""
    global _initialized
    if _initialized:
        try:
            _load_mt5().shutdown()
        except Exception:
            pass
        _initialized = False


# ── Symbol Mapping ───────────────────────────────────────────────────────

def _to_mt5_symbol(yf_symbol: str, config: Optional[dict] = None) -> str:
    """Convert a yfinance-style symbol to MT5 broker symbol.

    ``EURUSD=X`` → ``EURUSDm`` (auto-detects broker suffix if connected).
    """
    # Clean the input yfinance symbol first
    base = yf_symbol.replace("=X", "").replace("=x", "").replace("/", "").strip()
    if base.endswith("-USD"):
        base = base[:-4]

    # Attempt to query MT5 terminal dynamically if it's already active
    try:
        global _mt5, _initialized
        if _initialized and _mt5:
            from axonai.dataflows.config import get_config
            cfg = config or get_config()
            config_suffix = cfg.get("mt5_symbol_suffix", "m")
            if config_suffix and config_suffix.lower() == "none":
                config_suffix = ""
            
            # Try base as-is first (handles EURUSDm passed directly)
            info = _mt5.symbol_info(base)
            if info is not None:
                return base

            suffixes_to_try = [config_suffix, "", "m", "_i", ".pro", "_ecn"]
            # Filter duplicates but keep order
            seen = set()
            suffixes_to_try = [x for x in suffixes_to_try if not (x in seen or seen.add(x))]
            
            for suffix in suffixes_to_try:
                candidate = base + suffix
                if candidate == base:
                    continue  # already tried above
                info = _mt5.symbol_info(candidate)
                if info is not None:
                    return candidate
    except Exception as e:
        logger.debug("Failed to dynamically auto-detect broker symbol suffix: %s", e)

    # Fallback to configured default
    from axonai.dataflows.config import get_config
    cfg = config or get_config()
    suffix = cfg.get("mt5_symbol_suffix", "m")
    if suffix and suffix.lower() == "none":
        suffix = ""
    # Don't double-suffix if base already ends with the suffix
    if suffix and base.endswith(suffix):
        return base
    return base + suffix


def _ensure_symbol_visible(mt5_symbol: str) -> bool:
    """Make sure the symbol is in Market Watch."""
    mt5 = _load_mt5()
    info = mt5.symbol_info(mt5_symbol)
    if info is None:
        logger.warning("Symbol %s not found in MT5", mt5_symbol)
        return False
    if not info.visible:
        mt5.symbol_select(mt5_symbol, True)
    return True


# ── Live Price ───────────────────────────────────────────────────────────

def get_mt5_live_price(yf_symbol: str) -> Optional[Tuple[float, float, float]]:
    """Return (bid, ask, last) for the symbol, or None if unavailable."""
    if not mt5_initialize():
        return None
    mt5 = _load_mt5()
    sym = _to_mt5_symbol(yf_symbol)
    if not _ensure_symbol_visible(sym):
        return None
    tick = mt5.symbol_info_tick(sym)
    if tick is None:
        return None
    return (tick.bid, tick.ask, tick.last if tick.last > 0 else (tick.bid + tick.ask) / 2)


# ── OHLCV Bars & Ticks ─────────────────────────────────────────────────────

def get_mt5_ticks(yf_symbol: str, from_time: datetime, count: int = 1000) -> Optional[np.ndarray]:
    """Fetch raw ticks from MT5 using copy_ticks_from.
    Returns structured numpy array with fields: time, bid, ask, last, volume, flags.
    """
    if not mt5_initialize():
        return None
    mt5 = _load_mt5()
    sym = _to_mt5_symbol(yf_symbol)
    if not _ensure_symbol_visible(sym):
        return None
    ticks = mt5.copy_ticks_from(sym, from_time, count, mt5.COPY_TICKS_ALL)
    return ticks if ticks is not None and len(ticks) > 0 else None


def _fetch_bars(mt5_symbol: str, timeframe_key: str,
                start: datetime, end: datetime) -> Optional[pd.DataFrame]:
    """Fetch OHLCV bars from MT5 for a single timeframe."""
    mt5 = _load_mt5()
    tf = _TF_MAP.get(timeframe_key)
    if tf is None:
        logger.warning("Unknown timeframe: %s", timeframe_key)
        return None

    rates = mt5.copy_rates_range(mt5_symbol, tf, start, end)
    if rates is None or len(rates) == 0:
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "tick_volume": "Volume",
    }, inplace=True)
    return df[["Open", "High", "Low", "Close", "Volume"]]


def get_mt5_stock_data(
    symbol: Annotated[str, "ticker symbol"],
    start_date: Annotated[str, "Start date yyyy-mm-dd"],
    end_date: Annotated[str, "End date yyyy-mm-dd"],
) -> str:
    """Fetch multi-timeframe OHLCV from MT5 (M15, H1, H4, D1).

    Returns a formatted string with all four timeframes concatenated,
    matching the interface expected by the market analyst agent.
    """
    if not mt5_initialize():
        raise RuntimeError("MT5 not available")

    mt5_sym = _to_mt5_symbol(symbol)
    if not _ensure_symbol_visible(mt5_sym):
        raise RuntimeError(f"Symbol {mt5_sym} not found in MT5 Market Watch")

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)

    timeframes = [
        ("D1",  "Daily — overall range & swing levels"),
        ("H4",  "4-Hour — long-term intraday trends"),
        ("H1",  "1-Hour — intraday trend direction"),
        ("M15", "15-Min — trade entry retest detection"),
    ]

    sections: List[str] = []
    sections.append(
        f"# Multi-Timeframe OHLCV for {mt5_sym} "
        f"({start_date} to {end_date})\n"
        f"# Source: MetaTrader 5 (live)\n"
    )

    for tf_key, tf_desc in timeframes:
        df = _fetch_bars(mt5_sym, tf_key, start_dt, end_dt)
        if df is not None and not df.empty:
            # Limit output to last N bars to keep token count manageable
            max_bars = {"D1": 30, "H4": 60, "H1": 100, "M15": 100}
            limit = max_bars.get(tf_key, 50)
            df_tail = df.tail(limit).round(5)
            sections.append(f"\n## {tf_key} — {tf_desc} (last {len(df_tail)} bars)\n")
            sections.append(df_tail.to_csv())
        else:
            sections.append(f"\n## {tf_key} — {tf_desc}\nNo data available.\n")

    return "\n".join(sections)


# ── Technical Indicators ─────────────────────────────────────────────────

def get_mt5_indicators(
    symbol: Annotated[str, "ticker symbol"],
    indicator: Annotated[str, "technical indicator name"],
    curr_date: Annotated[str, "current trading date YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    """Calculate a technical indicator from MT5 bars using stockstats.

    Fetches H1 bars by default for indicator calculation, providing
    intraday granularity for the indicator values.
    """
    if not mt5_initialize():
        raise RuntimeError("MT5 not available")

    from .stockstats_utils import StockstatsUtils

    mt5_sym = _to_mt5_symbol(symbol)
    if not _ensure_symbol_visible(mt5_sym):
        raise RuntimeError(f"Symbol {mt5_sym} not found in MT5")

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d") + timedelta(days=1)
    # Extra buffer for indicator warm-up (200 bars min for SMA200)
    start_dt = end_dt - timedelta(days=look_back_days + 250)

    # Use H1 for indicator calculation (good granularity/noise trade-off)
    df = _fetch_bars(mt5_sym, "H1", start_dt, end_dt)
    if df is None or df.empty:
        return f"No MT5 data available for {mt5_sym} to calculate {indicator}"

    # stockstats needs lowercase columns
    df_calc = df.copy()
    df_calc.columns = [c.lower() for c in df_calc.columns]

    try:
        ss = StockstatsUtils(df_calc)
        values = ss.get(indicator)
    except Exception as e:
        return f"Error calculating {indicator} from MT5 data: {e}"

    # Filter to look_back window
    cutoff = datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=look_back_days)
    values = values[values.index >= cutoff]

    result = f"## {indicator} (H1 bars from MT5) — last {look_back_days} days:\n\n"
    for ts, val in values.items():
        result += f"{ts.strftime('%Y-%m-%d %H:%M')}: {val}\n"
    return result


# ── ATR Helper (used by Trader) ──────────────────────────────────────────

def get_mt5_atr(yf_symbol: str, period: int = 14,
                timeframe: str = "H1") -> Optional[float]:
    """Compute ATR from MT5 bars. Returns None if MT5 unavailable."""
    if not mt5_initialize():
        return None

    mt5_sym = _to_mt5_symbol(yf_symbol)
    if not _ensure_symbol_visible(mt5_sym):
        return None

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=60)
    df = _fetch_bars(mt5_sym, timeframe, start_dt, end_dt)
    if df is None or len(df) < period + 1:
        return None

    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.rolling(window=period).mean().iloc[-1]
    return float(atr) if pd.notna(atr) else None


def get_broker_tz_offset(symbol: str = "EURUSDm") -> int:
    """Get broker timezone offset from UTC in hours. Cached to prevent repeated MT5 calls.
    
    If the market is open, computes it dynamically by comparing the latest tick time
    to the system UTC clock. If the market is closed (e.g. weekend), falls back
    to the standard EET/EEST (UTC+2/UTC+3) US DST transition rules used by almost
    all MT5 forex brokers.
    """
    global _cached_tz_offset
    if _cached_tz_offset is not None:
        return _cached_tz_offset

    import time
    offset = None
    try:
        import MetaTrader5 as mt5
        # mt5_initialize is cached and safe to call
        if mt5_initialize():
            tick = mt5.symbol_info_tick(symbol)
            if tick is not None:
                tick_time = tick.time
                current_time = time.time()
                # If tick is within 2 hours of current time, compute offset directly
                if abs(tick_time - current_time) < 7200:
                    offset = int(round((tick_time - current_time) / 3600))
    except Exception as e:
        logger.debug("Failed to determine broker offset dynamically: %s", e)

    if offset is None:
        # Fallback: EET/EEST (UTC+2 / UTC+3) US DST rules
        from datetime import datetime, timezone, timedelta
        now_utc = datetime.now(timezone.utc)
        year = now_utc.year
        # US DST starts on second Sunday of March
        dst_start = datetime(year, 3, 8, 7, tzinfo=timezone.utc)
        while dst_start.weekday() != 6:
            dst_start += timedelta(days=1)
        # US DST ends on first Sunday of November
        dst_end = datetime(year, 11, 1, 6, tzinfo=timezone.utc)
        while dst_end.weekday() != 6:
            dst_end += timedelta(days=1)

        if dst_start <= now_utc < dst_end:
            offset = 3  # EEST
        else:
            offset = 2  # EET

    _cached_tz_offset = offset
    return offset

