"""Gate 2 dispatch — pass-through node that fans out to the 4 parallel L2 agents.

The builder adds static parallel edges from this node to evidence_analysis,
fact_reconstruction, witness_analysis, and legal_knowledge. LangGraph
executes all 4 in parallel and joins via gate2_join.
"""
from __future__ import annotations

from typing import Any

from src.pipeline.graph.state import GraphState


async def gate2_dispatch(state: GraphState) -> dict[str, Any]:
    """Pass-through; fan-out is handled by the builder's static parallel edges."""
    return {}
