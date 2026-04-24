"""Pre-run guardrail node — ported from src.pipeline.hooks.

Runs the input injection check and document-pages hydration before the
first agent fires. Skipped on resume (is_resume=True).
"""

from __future__ import annotations

import logging
from typing import Any

from src.pipeline.graph.state import GraphState
from src.shared.audit import append_audit_entry
from src.shared.case_state import CaseState

logger = logging.getLogger(__name__)


async def pre_run_guardrail(state: GraphState) -> dict[str, Any]:
    """Sanitize input and hydrate document pages before any agent runs."""
    case: CaseState = state["case"]

    if state.get("is_resume"):
        return {}

    # Document pages are populated by _run_case_pipeline from the already-loaded
    # Document ORM objects (intake extraction completes before the pipeline starts).
    # No DB session is needed here — rely on what's in the case state.
    missing_pages = [
        d.get("openai_file_id")
        for d in (case.raw_documents or [])
        if d.get("openai_file_id") and not d.get("pages")
    ]
    if missing_pages:
        logger.warning(
            "case_id=%s: %d document(s) have no pages in state at pipeline start: %s",
            case.case_id,
            len(missing_pages),
            missing_pages,
        )

    # ------------------------------------------------------------------
    # Input injection check (InputGuardrailHook.before_run)
    # ------------------------------------------------------------------
    description = case.case_metadata.get("description", "") if case.case_metadata else ""
    if description:
        try:
            from src.pipeline.guardrails import check_input_injection

            result = await check_input_injection(description)
            if result.get("blocked"):
                logger.warning(
                    "Input injection detected (method=%s, case_id=%s): %s",
                    result["method"],
                    case.case_id,
                    result["reason"],
                )
                updated_meta = {**case.case_metadata, "description": result["sanitized_text"]}
                case = case.model_copy(update={"case_metadata": updated_meta})
                case = append_audit_entry(
                    case,
                    agent="guardrails",
                    action="input_injection_blocked",
                    input_payload={"method": result["method"]},
                    output_payload={"reason": result["reason"]},
                )
        except Exception:
            logger.exception("Input injection check failed — proceeding with original description")

    return {"case": case}
