"""Gate 4 — hearing governance agent node."""
from __future__ import annotations
from typing import Any
from src.pipeline.graph.nodes.common import _run_agent_node
from src.pipeline.graph.state import GraphState


async def hearing_governance(state: GraphState) -> dict[str, Any]:
    return await _run_agent_node("hearing-governance", state)
