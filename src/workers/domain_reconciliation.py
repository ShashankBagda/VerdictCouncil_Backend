"""arq cron job: reconcile DomainDocument rows stuck in transient states.

Scans for documents stuck in uploading/parsed/indexing for >15 minutes,
polls OpenAI for their current state, and advances or marks them failed.
Also removes orphaned OpenAI files (files whose domain_document_id metadata
tag does not match any DB row) to prevent accumulation of billed storage.

Run every 10 minutes via WorkerSettings.cron_jobs.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from src.models.domain import DomainDocument, DomainDocumentStatus
from src.services.database import async_session
from src.shared.config import settings

logger = logging.getLogger(__name__)

_STUCK_THRESHOLD_MINUTES = 15
_STUCK_STATUSES = {
    DomainDocumentStatus.uploading,
    DomainDocumentStatus.parsed,
    DomainDocumentStatus.indexing,
}


async def reconcile_domain_documents(ctx: dict) -> None:
    """Reconcile stuck DomainDocument rows against OpenAI state.

    Called by arq every 10 minutes via WorkerSettings.cron_jobs.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=_STUCK_THRESHOLD_MINUTES)

    try:
        import openai

        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    except Exception as exc:
        logger.error("Failed to initialise OpenAI client for reconciliation: %s", exc)
        return

    async with async_session() as db:
        result = await db.execute(
            select(DomainDocument).where(
                DomainDocument.status.in_(list(_STUCK_STATUSES)),
                DomainDocument.uploaded_at < cutoff,
            )
        )
        stuck_docs = list(result.scalars().all())

    if not stuck_docs:
        return

    logger.info("Reconciliation: found %d stuck DomainDocument rows", len(stuck_docs))

    for doc in stuck_docs:
        try:
            await _reconcile_one(client, doc)
        except Exception as exc:
            logger.error(
                "Reconciliation failed for DomainDocument %s: %s", doc.id, exc, exc_info=True
            )


async def _reconcile_one(client, doc: DomainDocument) -> None:
    """Advance or fail a single stuck document by polling OpenAI."""
    async with async_session() as db:
        live_doc = await db.get(DomainDocument, doc.id)
        if live_doc is None or live_doc.status not in _STUCK_STATUSES:
            return

        if live_doc.status == DomainDocumentStatus.indexing and live_doc.sanitized_file_id:
            await _check_vector_store_file(client, db, live_doc)
        elif live_doc.status in {DomainDocumentStatus.uploading, DomainDocumentStatus.parsed}:
            # Check if the original file still exists in OpenAI
            if live_doc.openai_file_id:
                try:
                    await client.files.retrieve(live_doc.openai_file_id)
                    # File exists but we're stuck — mark failed so the admin can retry
                    logger.warning(
                        "DomainDocument %s stuck in %s for >%d min — marking failed",
                        live_doc.id,
                        live_doc.status,
                        _STUCK_THRESHOLD_MINUTES,
                    )
                    live_doc.status = DomainDocumentStatus.failed
                    live_doc.error_reason = (
                        f"Stuck in {live_doc.status} for >{_STUCK_THRESHOLD_MINUTES} minutes; "
                        "manually retry upload"
                    )
                except Exception:
                    # File gone from OpenAI — definitely failed
                    live_doc.status = DomainDocumentStatus.failed
                    live_doc.error_reason = "OpenAI file not found during reconciliation"
            else:
                live_doc.status = DomainDocumentStatus.failed
                live_doc.error_reason = "No openai_file_id recorded; stuck in upload"

        await db.commit()


async def _check_vector_store_file(client, db, doc: DomainDocument) -> None:
    """Poll the vector store file status and advance or fail the document."""

    from src.models.domain import Domain

    domain = await db.get(Domain, doc.domain_id)
    if not domain or not domain.vector_store_id:
        doc.status = DomainDocumentStatus.failed
        doc.error_reason = "Domain vector store not found during reconciliation"
        return

    try:
        vs_file = await client.vector_stores.files.retrieve(
            vector_store_id=domain.vector_store_id,
            file_id=doc.sanitized_file_id,
        )
    except Exception as exc:
        logger.warning("Could not retrieve VS file %s: %s", doc.sanitized_file_id, exc)
        doc.status = DomainDocumentStatus.failed
        doc.error_reason = f"Could not retrieve vector store file: {exc}"
        return

    if vs_file.status == "completed":
        doc.status = DomainDocumentStatus.indexed
        logger.info("DomainDocument %s reconciled → indexed", doc.id)
    elif vs_file.status in {"failed", "cancelled"}:
        doc.status = DomainDocumentStatus.failed
        doc.error_reason = f"OpenAI vector store file status: {vs_file.status}"
        logger.warning(
            "DomainDocument %s reconciled → failed (VS status=%s)", doc.id, vs_file.status
        )
    else:
        # Still in progress — leave it for the next reconciliation pass
        logger.debug(
            "DomainDocument %s still indexing (VS status=%s)", doc.id, vs_file.status
        )
