"""Per-judge knowledge base service using OpenAI Vector Stores."""

import contextlib
import logging

from openai import AsyncOpenAI

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
    """List all files in a judge's vector store."""
    client = _get_client()

    vs_files = await client.vector_stores.files.list(vector_store_id=vector_store_id)

    result = []
    for vs_file in vs_files.data:
        # Get file metadata
        try:
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
        except Exception:
            result.append(
                {
                    "file_id": vs_file.id,
                    "filename": "unknown",
                    "status": vs_file.status,
                }
            )

    return result


async def delete_kb_file(vector_store_id: str, file_id: str) -> bool:
    """Delete a file from a judge's vector store."""
    client = _get_client()
    await client.vector_stores.files.delete(vector_store_id=vector_store_id, file_id=file_id)
    # File may already be deleted — ignore failures here
    with contextlib.suppress(Exception):
        await client.files.delete(file_id)
    logger.info("Deleted file %s from vector store %s", file_id, vector_store_id)
    return True
