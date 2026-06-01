"""On-demand LangGraph invocation wrapper.

Manages a pre-compiled LangGraph instance and fires it
only when triggered by a MarketEvent from the event detector.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Optional, Tuple

from axonai.world_state import WorldState
from axonai.dataflows.evidence_extractor import MarketEvidence
from axonai.realtime.event_types import EventPriority, MarketEvent

logger = logging.getLogger(__name__)


class GraphExecutor:
    """Wraps AxonAIGraph for on-demand invocation from the event queue.

    Key optimizations:
    - Graph compiled ONCE at startup, reused for all invocations
    - WorldState and MarketEvidence taken as snapshots from LiveState
    - Cooldown enforced between invocations
    - Results logged to memory
    """

    def __init__(self, symbol: str, config: dict, callbacks: list = None):
        self.symbol = symbol
        self.config = config
        self.cooldown_seconds: int = config.get("realtime_cooldown_seconds", 300)
        self._callbacks = callbacks or []
        self._last_execution: datetime = datetime.min
        self._execution_count: int = 0
        self._graph = None
        self._min_priority = EventPriority[config.get("realtime_min_event_priority", "MEDIUM")]

    def compile_graph(self):
        """Compile graph once. Call during daemon startup."""
        from axonai.graph.trading_graph import AxonAIGraph

        selected_analysts = ["market", "fundamentals", "news", "social"]
        self._graph = AxonAIGraph(
            selected_analysts,
            config=self.config,
            debug=True,
            callbacks=self._callbacks,
        )
        logger.info("GraphExecutor: graph compiled successfully")

    def should_execute(self, event: MarketEvent) -> bool:
        """Check cooldown and priority threshold."""
        # Priority gate
        if event.priority.value < self._min_priority.value:
            return False

        # Cooldown gate
        elapsed = (datetime.now() - self._last_execution).total_seconds()
        if elapsed < self.cooldown_seconds:
            remaining = self.cooldown_seconds - elapsed
            logger.debug("GraphExecutor: cooldown active (%.0fs remaining)", remaining)
            return False

        return True

    def execute(
        self,
        event: MarketEvent,
        world_state: WorldState,
        market_evidence: MarketEvidence,
        macro_signal = None,
    ) -> Tuple[dict, str]:
        """Fire the full LangGraph pipeline.

        Returns (final_state, decision_signal).
        """
        if self._graph is None:
            raise RuntimeError("Graph not compiled. Call compile_graph() first.")

        self._last_execution = datetime.now()
        self._execution_count += 1

        # Convert MT5 symbol to yfinance format for graph compatibility
        yf_symbol = self._to_yf_symbol(self.symbol)
        trade_date = datetime.now().strftime("%Y-%m-%d")

        logger.info(
            "GraphExecutor: FIRING #%d for %s | event=%s | priority=%s",
            self._execution_count, yf_symbol,
            event.event_type.value, event.priority.name,
        )

        world_state_dict = asdict(world_state)
        if macro_signal:
            world_state_dict["macro_bias"] = macro_signal.bias
            world_state_dict["macro_confidence"] = macro_signal.confidence
            world_state_dict["macro_key_level"] = macro_signal.key_level
            world_state_dict["macro_bias_age_sec"] = macro_signal.age_sec
        else:
            world_state_dict["macro_bias"] = "HOLD"
            world_state_dict["macro_confidence"] = 0.0
            world_state_dict["macro_key_level"] = 0.0
            world_state_dict["macro_bias_age_sec"] = 999.0

        # Build initial state with event context injected
        initial_state = self._graph.propagator.create_initial_state(
            company_name=yf_symbol,
            trade_date=trade_date,
            asset_type="forex",
            past_context=self._graph.memory_log.get_past_context(yf_symbol) or "",
            world_state=world_state_dict,
            market_evidence=asdict(market_evidence),
        )

        # Inject event context into trader hypothesis
        initial_state["trader_hypothesis"]["event_trigger"] = {
            "type": event.event_type.value,
            "priority": event.priority.name,
            "price": event.price,
            "details": event.details,
        }

        # Get graph args
        graph_args = self._graph.propagator.get_graph_args(
            callbacks=self._callbacks
        )

        try:
            chunk_callback = self.config.get("realtime_chunk_callback")
            if chunk_callback:
                logger.info("GraphExecutor: executing via STREAM callback")
                final_state = {}
                for chunk in self._graph.graph.stream(initial_state, **graph_args):
                    chunk_callback(chunk)
                    if isinstance(chunk, dict):
                        # Some versions of LangGraph return dict with node names as keys
                        for node, state_update in chunk.items():
                            if isinstance(state_update, dict):
                                final_state.update(state_update)
                            else:
                                final_state[node] = state_update
                    else:
                        # Fallback
                        pass
            else:
                # Invoke (not stream — daemon doesn't need TUI)
                final_state = self._graph.graph.invoke(initial_state, **graph_args)

            # Process signal
            signal = self._graph.process_signal(
                final_state.get("final_trade_decision", "")
            )

            # Store in memory log
            self._graph.memory_log.store_decision(yf_symbol, trade_date, signal)

            logger.info("GraphExecutor: DECISION = %s (execution #%d)",
                        signal, self._execution_count)

            return final_state, signal

        except Exception as e:
            logger.error("GraphExecutor: execution failed: %s", e, exc_info=True)
            return {}, f"ERROR: {e}"

    def _to_yf_symbol(self, mt5_symbol: str) -> str:
        """Convert MT5 symbol back to yfinance format.
        EURUSDm -> EURUSD=X
        """
        base = mt5_symbol.replace("=X", "").replace("=x", "").strip()
        suffix = self.config.get("mt5_symbol_suffix", "m")
        if suffix and base.endswith(suffix):
            base = base[:-len(suffix)]
        return base + "=X"

    @property
    def is_ready(self) -> bool:
        return self._graph is not None

    @property
    def seconds_until_ready(self) -> float:
        """Seconds remaining in cooldown."""
        elapsed = (datetime.now() - self._last_execution).total_seconds()
        remaining = self.cooldown_seconds - elapsed
        return max(0.0, remaining)

    @property
    def execution_count(self) -> int:
        return self._execution_count
