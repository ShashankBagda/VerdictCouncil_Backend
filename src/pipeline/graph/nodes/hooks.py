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

    # ------------------------------------------------------------------
    # Document-pages hydration (DocumentPagesHydrationHook.before_run)
    # ------------------------------------------------------------------
    if case.raw_documents:
        try:
            from sqlalchemy import select

            from src.models.case import Document
            from src.services.database import async_session

            file_ids = [
                d.get("openai_file_id") for d in case.raw_documents if d.get("openai_file_id") and not d.get("pages")
            ]
            if file_ids:
                async with async_session() as db:
                    result = await db.execute(select(Document).where(Document.openai_file_id.in_(file_ids)))
                    docs_by_file_id = {d.openai_file_id: d for d in result.scalars().all()}

                new_raw = []
                for raw in case.raw_documents:
                    fid = raw.get("openai_file_id")
                    if fid and fid in docs_by_file_id and not raw.get("pages"):
                        db_pages = docs_by_file_id[fid].pages
                        if db_pages:
                            raw = {**raw, "pages": db_pages}
                    new_raw.append(raw)
                case = case.model_copy(update={"raw_documents": new_raw})
        except Exception:
            logger.exception("Document hydration failed — proceeding with original raw_documents")

    # ------------------------------------------------------------------
    # Input injection check (InputGuardrailHook.before_run)
    # ------------------------------------------------------------------
    description = case.case_metadata.get("description", "") if case.case_metadata else ""
    if description:
        try:
            from langchain_openai import ChatOpenAI

            from src.pipeline.guardrails import check_input_injection

            client = ChatOpenAI().openai_client  # type: ignore[attr-defined]
            result = await check_input_injection(description, client)
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
