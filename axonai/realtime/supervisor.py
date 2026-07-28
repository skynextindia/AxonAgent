"""Single-process multi-pair supervisor.

Runs one :class:`AxonDaemon` per currency pair as a background thread, all
sharing one MT5 connection, one account-global :class:`RiskGuard`, and one
dashboard (multiplexed by symbol). The supervisor owns the process-wide
concerns the individual daemons must NOT each perform: the MT5 connect/shutdown
lifecycle and the SIGINT/SIGTERM handler.

Each ``AxonDaemon.start()`` blocks in its own event loop, so every daemon gets
its own thread; the supervisor's main thread waits and coordinates shutdown.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from typing import Dict, List

from axonai.dataflows.mt5_data import mt5_initialize, mt5_shutdown
from axonai.realtime.risk_guard import RiskGuard
from axonai.realtime.daemon import AxonDaemon
from axonai.realtime.correlation_engine import CorrelationEngine
from axonai.realtime.alerts import send_alert

logger = logging.getLogger(__name__)


class DaemonSupervisor:
    """Owns N per-pair AxonDaemon threads over one shared MT5 connection."""

    def __init__(self, symbols: List[str], base_config: dict):
        self.base_config = base_config
        self.symbols = [s.strip() for s in symbols if s and s.strip()]
        if not self.symbols:
            raise ValueError("DaemonSupervisor requires at least one symbol")

        # One shared MT5 connection for the whole process (idempotent).
        mt5_initialize(
            terminal_path=base_config.get("mt5_terminal_path"),
            login=base_config.get("mt5_login"),
            password=base_config.get("mt5_password"),
            server=base_config.get("mt5_server"),
        )

        # One account-global drawdown breaker shared by every pair's executor,
        # so daily-drawdown limits are enforced across all pairs (not per-pair)
        # and the reports/daily_pnl.json file has a single writer.
        self.risk_guard = RiskGuard(base_config)

        # The cross-pair correlation engine (shared, lock-guarded). Computes an
        # initial calibration snapshot from H1 bars at construction.
        self.correlation_engine = CorrelationEngine(self.symbols, base_config)

        # Build one daemon per pair. Each AxonDaemon resolves its own per-pair
        # calibration internally; the engine adds vol-ratio-derived overrides.
        self.daemons: Dict[str, AxonDaemon] = {}
        for sym in self.symbols:
            self.daemons[sym] = AxonDaemon(
                sym,
                base_config,
                risk_guard=self.risk_guard,
                correlation_engine=self.correlation_engine,
                supervisor=self,
                config_overrides=self.correlation_engine.calibrated_overrides(sym),
            )

        # Lead-side order mirror: one shared client forwards every pair's
        # entry/close DECISIONS to the execution node (best-effort, fail-open).
        # Off unless mirror_enabled; an execution node itself never sets this.
        self.mirror_client = None
        if base_config.get("mirror_enabled"):
            from axonai.realtime.mirror_client import MirrorClient
            url = base_config.get("mirror_url", "ws://127.0.0.1:8770")
            self.mirror_client = MirrorClient(url)
            for d in self.daemons.values():
                d.mirror_client = self.mirror_client
            logger.info("DaemonSupervisor: order mirror ENABLED → %s", url)

        self._threads: List[threading.Thread] = []
        self._thread_by_symbol: Dict[str, threading.Thread] = {}
        self._dead_alerted: set = set()          # symbols already flagged as dead (no alert spam)
        self._stopping = threading.Event()
        self._stopped = False

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        """Spawn one thread per daemon and block until stopped."""
        # Process-level signal handling lives here; the daemons run off the main
        # thread and skip their own handler registration (they catch the
        # ValueError raised by signal.signal off-main-thread).
        try:
            signal.signal(signal.SIGINT, self._on_signal)
            signal.signal(signal.SIGTERM, self._on_signal)
        except ValueError:
            pass  # not on the main thread

        if self.mirror_client is not None:
            self.mirror_client.start()

        for sym, d in self.daemons.items():
            t = threading.Thread(
                target=self._run_daemon, args=(sym, d),
                name=f"AxonDaemon-{sym}", daemon=True,
            )
            t.start()
            self._threads.append(t)
            self._thread_by_symbol[sym] = t

        logger.info(
            "DaemonSupervisor: started %d pair(s): %s",
            len(self.daemons), ", ".join(self.daemons),
        )

        try:
            while not self._stopping.is_set():
                time.sleep(1)
                self._check_thread_liveness()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop_all()

    def _check_thread_liveness(self) -> None:
        """Detect a daemon thread that died outside of shutdown and act on it.

        The old loop only slept — a crashed pair thread was logged once by
        ``_run_daemon`` and then abandoned, so that pair's trailing / breakeven /
        EOD management silently stopped. The open position still carries its
        broker-side entry stop-loss (so it is NOT unprotected), but it will no
        longer be trailed, moved to breakeven, or flattened at EOD. The original
        bug was the *silence*; make it loud, and optionally flatten.
        """
        if self._stopping.is_set():
            return
        for sym, t in self._thread_by_symbol.items():
            if t.is_alive() or sym in self._dead_alerted:
                continue
            self._dead_alerted.add(sym)
            logger.critical(
                "DaemonSupervisor: pair %s daemon thread has DIED — trailing/breakeven/EOD "
                "are NO LONGER running for it. Open positions keep their broker SL but are "
                "no longer managed. Manual attention needed.", sym,
            )
            try:
                send_alert(
                    f"AxonAi: {sym} daemon thread DIED — stop-management halted for {sym}. "
                    f"Positions retain their entry SL but are no longer trailed/flattened. "
                    f"Restart required.", self.base_config,
                )
            except Exception as e:
                logger.error("DaemonSupervisor: liveness alert failed for %s: %s", sym, e)
            # Opt-in safety net: flatten the abandoned pair so no position is left
            # unmanaged. Off by default (the position still has its entry SL, and
            # auto-closing on a transient thread death may be undesirable).
            if self.base_config.get("supervisor_flatten_on_thread_death", False):
                try:
                    d = self.daemons.get(sym)
                    closed = d._close_all_positions("supervisor: daemon thread died") if d else 0
                    logger.critical(
                        "DaemonSupervisor: flattened %s after thread death (%d position(s) closed).",
                        sym, closed,
                    )
                except Exception as e:
                    logger.error("DaemonSupervisor: flatten-on-death failed for %s: %s", sym, e)

    def _run_daemon(self, symbol: str, daemon: AxonDaemon) -> None:
        try:
            daemon.start()  # blocks in its event loop until daemon.stop()
        except Exception as e:
            logger.error("DaemonSupervisor: daemon %s crashed: %s", symbol, e, exc_info=True)

    def _on_signal(self, signum, frame):
        logger.info("DaemonSupervisor: received signal %s; shutting down", signum)
        self._stopping.set()

    def stop_all(self) -> None:
        """Stop every daemon, then disconnect the shared MT5 connection once."""
        if self._stopped:
            return
        self._stopping.set()
        for sym, d in self.daemons.items():
            try:
                d.stop()
            except Exception as e:
                logger.error("DaemonSupervisor: error stopping %s: %s", sym, e)
        if self.mirror_client is not None:
            try:
                self.mirror_client.stop()
            except Exception as e:
                logger.error("DaemonSupervisor: error stopping mirror client: %s", e)
        # The supervisor owns the shared, process-wide MT5 connection.
        mt5_shutdown()
        self._stopped = True
        logger.info("DaemonSupervisor: all daemons stopped, MT5 disconnected.")
