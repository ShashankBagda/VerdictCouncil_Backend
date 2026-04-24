"""Gate 2 join — implicit barrier node.

LangGraph fires this node only after all 4 parallel L2 agents complete.
The _merge_case reducer has already accumulated their outputs into state["case"].
This node runs a governance integrity check (ported from GovernanceHaltHook
for the Gate-2 stage) and surfaces warnings for the builder's conditional edge.
"""
from __future__ import annotations

import logging
from typing import Any

from src.pipeline.graph.state import GraphState
from src.pipeline.guardrails import validate_output_integrity
from src.shared.audit import append_audit_entry
from src.shared.case_state import CaseState

logger = logging.getLogger(__name__)


async def gate2_join(state: GraphState) -> dict[str, Any]:
    """Validate Gate-2 outputs and surface any integrity issues."""
    case: CaseState = state["case"]
    integrity = validate_output_integrity(case.model_dump())
    if not integrity["passed"]:
        logger.warning(
            "Gate-2 output integrity check FAILED (case_id=%s): %s",
            case.case_id,
            integrity["issues"],
        )
        case = append_audit_entry(
            case,
            agent="guardrails",
            action="gate2_integrity_warning",
            output_payload=integrity,
        )
        return {"case": case}
    return {}
