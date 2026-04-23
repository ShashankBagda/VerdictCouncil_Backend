"""Per-judge and per-domain knowledge base service using OpenAI Vector Stores."""

import logging

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.user import User
from src.shared.config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def create_judge_vector_store(judge_id: str) -> str:
    """Create a new vector store for a judge. Returns the store ID."""
    client = _get_client()
    store = await client.vector_stores.create(
        name=f"vc-judge-{judge_id}",
        metadata={"judge_id": judge_id, "app": "verdictcouncil"},
    )
    logger.info("Created vector store %s for judge %s", store.id, judge_id)
    return store.id


async def ensure_judge_vector_store(db: AsyncSession, user: User) -> tuple[str, bool]:
    """Return ``(store_id, created)`` for the judge, provisioning one if missing.

    Takes a row-level lock on the user and re-reads state from the DB
    (``populate_existing=True``) so a second concurrent request sees the store
    created by the first — otherwise SQLAlchemy's identity map returns the
    stale in-memory ``User`` and the race window stays open.
    """
    locked = (
        await db.execute(
            select(User)
            .where(User.id == user.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if locked is None:
        raise LookupError(f"User {user.id} not found while locking for KB init")

    if locked.knowledge_base_vector_store_id:
        return locked.knowledge_base_vector_store_id, False

    store_id = await create_judge_vector_store(str(locked.id))
    locked.knowledge_base_vector_store_id = store_id
    await db.flush()
    return store_id, True


async def upload_document_to_kb(vector_store_id: str, file_bytes: bytes, filename: str) -> dict:
    """Upload a document to a judge's vector store. Returns file metadata."""
    client = _get_client()

    # Upload file to OpenAI
    file_obj = await client.files.create(
        file=(filename, file_bytes),
        purpose="assistants",
    )

    # Add file to vector store and poll until indexed
    vs_file = await client.vector_stores.files.create_and_poll(
        vector_store_id=vector_store_id,
        file_id=file_obj.id,
    )

    logger.info(
        "Uploaded %s to vector store %s (file_id=%s)", filename, vector_store_id, file_obj.id
    )
    return {
        "file_id": file_obj.id,
        "filename": filename,
        "status": vs_file.status,
        "bytes": len(file_bytes),
    }


async def search_kb(vector_store_id: str, query: str, max_results: int = 5) -> list[dict]:
    """Search a judge's vector store. Returns list of results."""
    client = _get_client()

    results = await client.vector_stores.search(
        vector_store_id=vector_store_id,
        query=query,
        max_num_results=max_results,
    )

    return [
        {
            "file_id": r.file_id,
            "filename": r.filename,
            "content": r.content[0].text if r.content else "",
            "score": r.score,
        }
        for r in results.data
    ]


async def list_kb_files(vector_store_id: str) -> list[dict]:
    """List all files in a judge's vector store.

    Iterates every page from the OpenAI paginator — a naked ``await`` only
    returns the first page (default 20), which would silently truncate larger
    KBs. Propagates any OpenAI failure so the route layer can surface a 503
    rather than returning degraded rows with fabricated filenames.
    """
    client = _get_client()

    result: list[dict] = []
    async for vs_file in await client.vector_stores.files.list(vector_store_id=vector_store_id):
        file_obj = await client.files.retrieve(vs_file.id)
        result.append(
            {
                "file_id": vs_file.id,
                "filename": file_obj.filename,
                "status": vs_file.status,
                "bytes": file_obj.bytes,
                "created_at": file_obj.created_at,
            }
        )
    return result


async def delete_kb_file(vector_store_id: str, file_id: str) -> bool:
    """Delete a file from a judge's vector store.

    Both the vector-store association and the raw file are removed. Any
    failure in either step propagates so the caller can retry — silently
    swallowing a ``files.delete`` error would leave an orphan file consuming
    OpenAI quota while the API reports success.
    """
    client = _get_client()
    await client.vector_stores.files.delete(vector_store_id=vector_store_id, file_id=file_id)
    await client.files.delete(file_id)
    logger.info("Deleted file %s from vector store %s", file_id, vector_store_id)
    return True


# ---------------------------------------------------------------------------
# Per-domain vector store wrappers
# ---------------------------------------------------------------------------


async def create_domain_vector_store(domain_code: str) -> str:
    """Create a new vector store for a domain. Returns the store ID."""
    client = _get_client()
    store = await client.vector_stores.create(
        name=f"domain-{domain_code}-{settings.namespace}",
        metadata={"domain_code": domain_code, "env": settings.namespace, "app": "verdictcouncil"},
    )
    logger.info("Created domain vector store %s for domain %s", store.id, domain_code)
    return store.id


async def ensure_domain_vector_store(db: AsyncSession, domain_id: str) -> tuple[str, bool]:
    """Return ``(store_id, created)`` for the domain, provisioning one if missing.

    Takes a row-level lock to prevent duplicate creation in concurrent requests.
    """
    from sqlalchemy import select as sa_select

    from src.models.domain import Domain

    locked = (
        await db.execute(
            sa_select(Domain)
            .where(Domain.id == domain_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if locked is None:
        raise LookupError(f"Domain {domain_id} not found")

    if locked.vector_store_id:
        if not locked.is_active:
            locked.is_active = True
            await db.flush()
        return locked.vector_store_id, False

    store_id = await create_domain_vector_store(locked.code)
    locked.vector_store_id = store_id
    locked.is_active = True
    await db.flush()
    return store_id, True
