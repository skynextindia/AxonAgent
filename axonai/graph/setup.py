# AxonAI/graph/setup.py

from typing import Any, Dict
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from axonai.agents import *
from axonai.agents.utils.agent_states import AgentState

from .analyst_execution import build_analyst_execution_plan
from .conditional_logic import ConditionalLogic


def evidence_compressor_node(state: AgentState) -> dict:
    parts = []
    report_keys = ["market_report", "sentiment_report", "news_report", "fundamentals_report"]
    for k in report_keys:
        val = state.get(k) or ""
        label = k.replace("_", " ").title()
        parts.append(f"### {label} (Truncated):\n{val[:4000]}")

    from langchain_core.messages import RemoveMessage, HumanMessage
    removal_ops = [RemoveMessage(id=m.id) for m in state.get("messages", [])]

    return {
        "compressed_evidence": "\n\n".join(parts),
        "messages": removal_ops + [HumanMessage(content="Continue")]
    }


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: Any,
        deep_thinking_llm: Any,
        tool_nodes: Dict[str, ToolNode],
        conditional_logic: ConditionalLogic,
        analyst_concurrency_limit: int = 1,
    ):
        """Initialize with required components."""
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.tool_nodes = tool_nodes
        self.conditional_logic = conditional_logic
        self.analyst_concurrency_limit = analyst_concurrency_limit

    def setup_graph(
        self, selected_analysts=["market", "social", "news", "fundamentals"]
    ):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include. Options are:
                - "market": Market analyst
                - "social": Social media analyst
                - "news": News analyst
                - "fundamentals": Fundamentals analyst
        """
        plan = build_analyst_execution_plan(
            selected_analysts,
            concurrency_limit=self.analyst_concurrency_limit,
        )

        analyst_factories = {
            "market": lambda: create_market_analyst(self.quick_thinking_llm),
            "social": lambda: create_sentiment_analyst(self.quick_thinking_llm),
            "news": lambda: create_news_analyst(self.quick_thinking_llm),
            "fundamentals": lambda: create_fundamentals_analyst(self.quick_thinking_llm),
        }

        # Create researcher and manager nodes
        bull_researcher_node = create_bull_researcher(self.quick_thinking_llm)
        bear_researcher_node = create_bear_researcher(self.quick_thinking_llm)
        research_manager_node = create_research_manager(self.deep_thinking_llm)
        trader_node = create_trader(self.quick_thinking_llm)

        # Create risk analysis nodes
        aggressive_analyst = create_aggressive_debator(self.quick_thinking_llm)
        neutral_analyst = create_neutral_debator(self.quick_thinking_llm)
        conservative_analyst = create_conservative_debator(self.quick_thinking_llm)
        portfolio_manager_node = create_portfolio_manager(self.deep_thinking_llm)

        # Create workflow
        workflow = StateGraph(AgentState)

        # Add analyst nodes to the graph
        for spec in plan.specs:
            workflow.add_node(spec.agent_node, analyst_factories[spec.key]())
            workflow.add_node(spec.clear_node, create_msg_delete())
            workflow.add_node(spec.tool_node, self.tool_nodes[spec.key])

        # Add other nodes
        workflow.add_node("Bull Researcher", bull_researcher_node)
        workflow.add_node("Bear Researcher", bear_researcher_node)
        workflow.add_node("Research Manager", research_manager_node)
        workflow.add_node("Trader", trader_node)
        workflow.add_node("Aggressive Analyst", aggressive_analyst)
        workflow.add_node("Neutral Analyst", neutral_analyst)
        workflow.add_node("Conservative Analyst", conservative_analyst)
        workflow.add_node("Portfolio Manager", portfolio_manager_node)
        workflow.add_node("Evidence Compressor", evidence_compressor_node)

        # Define edges (Directed Acyclic Graph topology with parallel paths)
        # Start with the Trader first to create a hypothesis
        workflow.add_edge(START, "Trader")

        # Connect Trader to each Analyst node in parallel
        for spec in plan.specs:
            workflow.add_edge("Trader", spec.agent_node)

        # Analyst loops: Analyst -> tool or clear
        for spec in plan.specs:
            current_analyst = spec.agent_node
            current_tools = spec.tool_node
            current_clear = spec.clear_node

            # Add conditional edges for current analyst (calls tool_node or clear_node)
            workflow.add_conditional_edges(
                current_analyst,
                getattr(self.conditional_logic, f"should_continue_{spec.key}"),
                [current_tools, current_clear],
            )
            workflow.add_edge(current_tools, current_analyst)

            # Connect each analyst's clear node to the Evidence Compressor
            workflow.add_edge(current_clear, "Evidence Compressor")

        # Evidence Compressor to Bull & Bear Researchers in parallel
        workflow.add_edge("Evidence Compressor", "Bull Researcher")
        workflow.add_edge("Evidence Compressor", "Bear Researcher")

        # Bull & Bear Researchers to Research Manager (fan-in)
        workflow.add_edge("Bull Researcher", "Research Manager")
        workflow.add_edge("Bear Researcher", "Research Manager")

        # Research Manager to the three risk analysts in parallel
        workflow.add_edge("Research Manager", "Aggressive Analyst")
        workflow.add_edge("Research Manager", "Conservative Analyst")
        workflow.add_edge("Research Manager", "Neutral Analyst")

        # Risk analysts to Portfolio Manager (fan-in)
        workflow.add_edge("Aggressive Analyst", "Portfolio Manager")
        workflow.add_edge("Conservative Analyst", "Portfolio Manager")
        workflow.add_edge("Neutral Analyst", "Portfolio Manager")

        # PM to end
        workflow.add_edge("Portfolio Manager", END)

        return workflow

