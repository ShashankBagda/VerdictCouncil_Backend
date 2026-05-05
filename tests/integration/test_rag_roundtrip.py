"""End-to-end RAG roundtrip integration test.

Creates a real OpenAI vector store, uploads a fixture file, waits for indexing,
then runs a file_search query and asserts the canary token is retrieved.

Requires OPENAI_API_KEY in the environment. Excluded from default CI — CI runs
tests/unit/ and tests/api/ only. Run locally with:

    OPENAI_API_KEY=sk-... make test-integration

or:

    OPENAI_API_KEY=sk-... pytest tests/integration/test_rag_roundtrip.py -v -m integration

Set VC_SKIP_LIVE_RAG=1 to skip in environments where live API calls are unwanted.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from pathlib import Path

import pytest
from openai import AsyncOpenAI

from src.services.knowledge_base import upload_document_to_kb
from src.shared.config import settings
from src.tools.vector_store_fallback import vector_store_search

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY not set — skipping live RAG test",
    ),
    pytest.mark.skipif(
        os.getenv("VC_SKIP_LIVE_RAG") == "1",
        reason="VC_SKIP_LIVE_RAG=1 — live RAG tests opted out",
    ),
]

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_legal_snippet.txt"
_CANARY = "ROUNDTRIP-CANARY-7421"


@pytest.fixture
async def temp_vector_store():
    """Create a throwaway vector store for the test and clean up afterward.

    Yields (store_id, mutable file_ids list). The test appends uploaded file
    ids so the fixture can delete them during teardown even if the test fails.

    Store names use the vc-test- prefix (not vc-judge-) so stale stores can be
    identified and swept manually: client.vector_stores.list() | grep vc-test-.
    """
    api_key = settings.openai_api_key
    client = AsyncOpenAI(api_key=api_key)
    store_name = f"vc-test-roundtrip-{uuid.uuid4().hex[:8]}"
    store = await client.vector_stores.create(
        name=store_name,
        metadata={"app": "verdictcouncil", "purpose": "integration-test"},
    )
    file_ids: list[str] = []

    try:
        yield store.id, file_ids
    finally:
        # Delete vector store first (detaches files), then the raw file objects.
        with contextlib.suppress(Exception):
            await client.vector_stores.delete(store.id)
        for fid in file_ids:
            with contextlib.suppress(Exception):
                await client.files.delete(fid)


async def test_vector_store_roundtrip(temp_vector_store):
    """Ingest a fixture into a real OpenAI vector store and assert retrieval.

    The fixture embeds a unique canary token (ROUNDTRIP-CANARY-7421) that
    cannot appear in any other document, so the assertion cannot pass on
    stale or cross-contaminated state.
    """
    store_id, file_ids = temp_vector_store

    # --- Ingest ---
    fixture_bytes = _FIXTURE_PATH.read_bytes()
    metadata = await upload_document_to_kb(store_id, fixture_bytes, _FIXTURE_PATH.name)
    file_ids.append(metadata["file_id"])
    assert metadata["status"] == "completed", f"Indexing did not complete: {metadata!r}"

    # --- Retrieve ---
    results = await vector_store_search(
        query="improper lane change Road Traffic Act offence",
        domain="traffic",
        max_results=3,
        vector_store_id=store_id,
    )

    # --- Assert ---
    assert len(results) >= 1, "Expected at least one result from the vector store"

    hit = any(
        _CANARY in (r.get("reasoning_summary") or "") or _CANARY in (r.get("citation") or "")
        for r in results
    )
    assert hit, (
        f"Canary token '{_CANARY}' not found in any result. "
        f"Got {len(results)} result(s): {[r.get('reasoning_summary', '')[:80] for r in results]}"
    )
