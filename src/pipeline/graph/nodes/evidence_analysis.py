"""Gate 2 parallel — evidence analysis agent node."""

from __future__ import annotations

from typing import Any

from src.pipeline.graph.nodes.common import _run_agent_node
from src.pipeline.graph.state import GraphState


async def evidence_analysis(state: GraphState) -> dict[str, Any]:
    return await _run_agent_node("evidence-analysis", state)
