"""Q2.1 — worker-side document parse + persist.

Runs `parse_document` against an uploaded document's `openai_file_id`
and writes the cacheable subset (`text`, `pages`, `tables`) onto
`documents.parsed_text`. Called from the `document_parse` arq task.

Failure policy (per Q2.1 acceptance: "log a warning, runner fallback
kicks in"):
- Missing `Document` row → log + return.
- Missing `openai_file_id` → log + return; nothing to parse.
- `DocumentParseError` → log warning, leave `parsed_text` NULL,
  return cleanly. The job marks `completed` (one-shot semantics —
  no arq retry burning OpenAI calls). Q2.2's runner-side fallback
  re-parses lazily on first pipeline use.
- Anything else (programming bug, DB outage) → re-raise so the
  outbox marks the job failed and an operator notices.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.case import Document
from src.tools.parse_document import DocumentParseError, parse_document

logger = logging.getLogger(__name__)

# `parse_document` returns a dict with non-JSON-safe fields
# (`SanitizationResult`). Only persist the cacheable subset that
# `CaseState.raw_documents` hydration cares about.
_PERSISTED_KEYS: tuple[str, ...] = ("text", "pages", "tables")


async def parse_and_persist_document(db: AsyncSession, *, document_id: uuid.UUID) -> None:
    document = await db.get(Document, document_id)
    if document is None:
        logger.warning("document_parse: Document %s not found; skipping", document_id)
        return
    if not document.openai_file_id:
        logger.warning(
            "document_parse: Document %s has no openai_file_id; skipping (runner fallback)",
            document_id,
        )
        return

    try:
        parsed = await parse_document(document.openai_file_id)
    except DocumentParseError as exc:
        logger.warning(
            "document_parse: parse_document failed for document=%s file=%s: %s; "
            "leaving parsed_text NULL (runner fallback will re-parse on demand)",
            document_id,
            document.openai_file_id,
            exc,
        )
        return

    document.parsed_text = {key: parsed.get(key) for key in _PERSISTED_KEYS}
    await db.commit()
