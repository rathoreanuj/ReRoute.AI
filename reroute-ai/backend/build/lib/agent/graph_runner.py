"""LangGraph graph execution — delegates to service/agent_graph_service.py.

This module exists for backwards compatibility. The actual graph implementation
lives in ``service.agent_graph_service``.
"""

from __future__ import annotations

from service.agent_graph_service import run_confirm_graph, run_propose_graph

__all__ = ["run_propose_graph", "run_confirm_graph"]
