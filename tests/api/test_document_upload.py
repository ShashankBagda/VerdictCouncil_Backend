"""Q2.1 — upload handlers enqueue per-document parse jobs.

Locks the contract that every Document with an `openai_file_id`
gets a `PipelineJobType.document_parse` outbox row in the same
transaction as the Document INSERT. Documents that failed the
OpenAI Files upload (no `openai_file_id`) are skipped so the
runner-side fallback (Q2.2) handles them lazily.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import app
from src.api.deps import get_current_user, get_db
from src.models.case import Case, CaseStatus, Document, DocumentKind
from src.models.pipeline_job import PipelineJobType
from src.models.user import User, UserRole

JUDGE_ID = uuid.uuid4()
CASE_ID = uuid.uuid4()


def _make_auth_override():
    user = MagicMock(spec=User)
    user.id = JUDGE_ID
    user.role = UserRole.judge

    async def _override():
        return user

    return _override


def _make_case(status: CaseStatus) -> MagicMock:
    case = MagicMock(spec=Case)
    case.id = CASE_ID
    case.created_by = JUDGE_ID
    case.status = status
    case.gate_state = None
    return case


def _override_db(case: MagicMock):
    """Mock AsyncSession that supports the upload route's call pattern.

    `flush` simulates the post-INSERT id assignment so per-document
    enqueue payloads carry a real UUID in `target_id`.
    """

    async def _gen():
        added: list[Document] = []

        def _add(obj):
            if isinstance(obj, Document):
                if obj.id is None:
                    obj.id = uuid.uuid4()
                # Mimic the SQLAlchemy server_default kicked in at INSERT time.
                if obj.kind is None:
                    obj.kind = DocumentKind.other
            added.append(obj)

        async def _flush():
            for obj in added:
                if isinstance(obj, Document) and obj.id is None:
                    obj.id = uuid.uuid4()

        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=case)
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)
        session.add = MagicMock(side_effect=_add)
        session.flush = AsyncMock(side_effect=_flush)
        session.refresh = AsyncMock()
        session.commit = AsyncMock()
        yield session

    return _gen


@asynccontextmanager
async def _patched_openai(file_ids: list[str | None]):
    """Patch openai.AsyncOpenAI so each upload returns the next file_id
    (or raises if the slot is None)."""
    calls = iter(file_ids)
    client = AsyncMock()
    client.files = AsyncMock()

    async def _create(**_kwargs):
        nxt = next(calls)
        if nxt is None:
            raise RuntimeError("OpenAI Files API down")
        return SimpleNamespace(id=nxt)

    client.files.create = AsyncMock(side_effect=_create)
    with patch("openai.AsyncOpenAI", return_value=client):
        yield


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _files_payload(n: int) -> list:
    return [
        ("files", (f"doc{i}.pdf", b"%PDF-fake content", "application/pdf"))
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_upload_documents_enqueues_one_parse_job_per_file() -> None:
    case = _make_case(CaseStatus.draft)
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(case)

    enqueue_mock = AsyncMock()
    async with _patched_openai(["file-a", "file-b"]):
        with patch("src.workers.outbox.enqueue_outbox_job", new=enqueue_mock):
            async with _client() as c:
                r = await c.post(
                    f"/api/v1/cases/{CASE_ID}/documents",
                    files=_files_payload(2),
                )

    assert r.status_code == 201, r.text

    parse_calls = [
        call
        for call in enqueue_mock.await_args_list
        if call.kwargs.get("job_type") is PipelineJobType.document_parse
    ]
    assert len(parse_calls) == 2
    target_ids = {call.kwargs["target_id"] for call in parse_calls}
    assert len(target_ids) == 2  # one per Document, distinct
    for call in parse_calls:
        assert call.kwargs["case_id"] == CASE_ID


@pytest.mark.asyncio
async def test_upload_skips_parse_job_when_openai_upload_failed() -> None:
    """No `openai_file_id` → no parse job (nothing to parse). Runner
    fallback handles it."""
    case = _make_case(CaseStatus.draft)
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(case)

    enqueue_mock = AsyncMock()
    # First file uploads OK, second fails the OpenAI Files API call.
    async with _patched_openai(["file-a", None]):
        with patch("src.workers.outbox.enqueue_outbox_job", new=enqueue_mock):
            async with _client() as c:
                r = await c.post(
                    f"/api/v1/cases/{CASE_ID}/documents",
                    files=_files_payload(2),
                )

    assert r.status_code == 201, r.text

    parse_calls = [
        call
        for call in enqueue_mock.await_args_list
        if call.kwargs.get("job_type") is PipelineJobType.document_parse
    ]
    assert len(parse_calls) == 1


@pytest.mark.asyncio
async def test_supplementary_upload_enqueues_one_parse_job_per_file() -> None:
    case = _make_case(CaseStatus.processing)
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(case)

    enqueue_mock = AsyncMock()
    async with _patched_openai(["file-x", "file-y"]):
        with patch("src.workers.outbox.enqueue_outbox_job", new=enqueue_mock):
            async with _client() as c:
                r = await c.post(
                    f"/api/v1/cases/{CASE_ID}/supplementary-documents",
                    files=_files_payload(2),
                )

    assert r.status_code == 201, r.text

    parse_calls = [
        call
        for call in enqueue_mock.await_args_list
        if call.kwargs.get("job_type") is PipelineJobType.document_parse
    ]
    assert len(parse_calls) == 2
