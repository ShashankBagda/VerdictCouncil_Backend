"""Q2.6 — partial integration coverage of the Q2 ingestion stack
against a real Postgres.

These tests exercise the layers the unit suites cannot reach:
- the real `documents.parsed_text` JSONB column and its round-trip
  through SQLAlchemy + the `Document` model,
- the real `pipeline_jobs` outbox INSERT shape (FK validity, JSONB
  payload, `target_id` carrying the document UUID),
- the real `_hydrate_raw_documents` helper running against a live
  session (identity-map back-fill semantics).

Each test is designed to fail under a specific Q2.x revert:
- `test_process_returns_409_when_no_parties_and_no_intake_extraction`
  → fails if Q2.5's guard is removed.
- `test_upload_inserts_document_parse_outbox_row_with_target_id`
  → fails if Q2.1's per-document `enqueue_outbox_job` loop is removed.
- `test_hydrate_uses_cached_parsed_text_against_real_session`
  → fails if Q2.2's hydration helper is reverted.

OpenAI is mocked via `openai.AsyncOpenAI`; this suite does not
consume credits and does not exercise the intake agent. The full
behavioral e2e (real intake LLM run on a failing-case payload,
SSE assertion on `IntakeOutput`) is deferred — see
`tasks/q2.6-deferral-2026-04-26.md`.

Skipped in default CI; set `INTEGRATION_TESTS=1` and run after
`make infra-up && make migrate` so 0027 is applied.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.api.app import app
from src.api.deps import get_current_user, get_db
from src.api.routes.cases import _hydrate_raw_documents
from src.models.case import Case, CaseDomain, CaseStatus, Document
from src.models.pipeline_job import PipelineJob, PipelineJobStatus, PipelineJobType
from src.models.user import User, UserRole
from src.services.database import async_session

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_TESTS") != "1",
    reason="Integration tests require infrastructure (set INTEGRATION_TESTS=1)",
)


@asynccontextmanager
async def _patched_openai_files(file_ids: list[str]):
    """Patch the OpenAI Files client so upload doesn't touch the real
    API. Each call returns the next file_id from the queue."""
    queue = iter(file_ids)
    client = AsyncMock()
    client.files = AsyncMock()

    async def _create(**_kwargs):
        return SimpleNamespace(id=next(queue))

    client.files.create = AsyncMock(side_effect=_create)
    with patch("openai.AsyncOpenAI", return_value=client):
        yield


async def _seed_user_and_case(
    session,
    *,
    domain: CaseDomain = CaseDomain.traffic_violation,
    status: CaseStatus = CaseStatus.pending,
    intake_extraction: dict | None = None,
) -> tuple[User, Case]:
    user = User(
        id=uuid.uuid4(),
        name="Q2.6 Judge",
        email=f"q26-{uuid.uuid4()}@example.com",
        role=UserRole.judge,
        password_hash="x",
    )
    session.add(user)
    await session.flush()

    case = Case(
        id=uuid.uuid4(),
        domain=domain,
        title="Q2.6 case",
        description="Integration test case for the Q2 ingestion stack.",
        status=status,
        created_by=user.id,
        intake_extraction=intake_extraction,
    )
    session.add(case)
    await session.flush()
    return user, case


async def _seed_document(
    session,
    *,
    case: Case,
    parsed_text: dict | None = None,
    openai_file_id: str | None = "file-fake",
) -> Document:
    document = Document(
        id=uuid.uuid4(),
        case_id=case.id,
        filename="evidence.pdf",
        file_type="application/pdf",
        openai_file_id=openai_file_id,
        parsed_text=parsed_text,
    )
    session.add(document)
    await session.flush()
    return document


def _override_user(user: User):
    async def _override():
        return user

    return _override


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_process_returns_409_when_no_parties_and_no_intake_extraction():
    """Q2.5 gate against real Postgres. A case with documents but no
    parties + no intake_extraction must 409 — `selectinload(Case.parties)`
    is exercised against the real schema (no async-lazy-load surprise)."""
    async with async_session() as session:
        user, case = await _seed_user_and_case(session)
        await _seed_document(session, case=case, parsed_text=None)
        await session.commit()
        case_id = case.id

    app.dependency_overrides[get_current_user] = _override_user(user)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(f"/api/v1/cases/{case_id}/process")

    assert r.status_code == 409
    assert "Intake confirmation incomplete" in r.json()["detail"]


@pytest.mark.asyncio
async def test_upload_inserts_document_parse_outbox_row_with_target_id():
    """Q2.1 outbox contract against real Postgres. After upload, the
    `pipeline_jobs` table must contain one `document_parse` row per
    Document with `target_id=document.id`. Catches FK violations,
    JSONB payload shape drift, and missing enqueue calls."""
    async with async_session() as session:
        user, case = await _seed_user_and_case(session)
        await session.commit()
        case_id = case.id

    app.dependency_overrides[get_current_user] = _override_user(user)
    files_payload = [
        ("files", ("notice.pdf", b"%PDF-1.4 fake", "application/pdf")),
        ("files", ("witness.pdf", b"%PDF-1.4 fake", "application/pdf")),
    ]
    async with _patched_openai_files(["file-a", "file-b"]), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/api/v1/cases/{case_id}/documents",
            files=files_payload,
        )
    assert r.status_code == 201, r.text

    async with async_session() as session:
        documents = (
            (await session.execute(select(Document).where(Document.case_id == case_id)))
            .scalars()
            .all()
        )
        assert len(documents) == 2

        parse_jobs = (
            (
                await session.execute(
                    select(PipelineJob).where(
                        PipelineJob.case_id == case_id,
                        PipelineJob.job_type == PipelineJobType.document_parse,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(parse_jobs) == 2
        target_ids = {job.target_id for job in parse_jobs}
        assert target_ids == {doc.id for doc in documents}
        for job in parse_jobs:
            assert job.status == PipelineJobStatus.pending


@pytest.mark.asyncio
async def test_hydrate_uses_cached_parsed_text_against_real_session():
    """Q2.2 hydration against real SQLAlchemy. Cache hit returns the
    cached `text`; cache miss triggers `parse_and_persist_document`
    (mocked OpenAI) and the entry picks up the back-filled value via
    the session's identity map."""
    async with async_session() as session:
        _, case = await _seed_user_and_case(session)
        cached = await _seed_document(
            session,
            case=case,
            parsed_text={
                "text": "cached evidence",
                "pages": [{"page_number": 1, "text": "cached evidence", "tables": []}],
                "tables": [],
            },
        )
        miss = await _seed_document(session, case=case, parsed_text=None)
        await session.commit()
        cached_id = cached.id
        miss_id = miss.id
        case_id = case.id

    async with async_session() as session:
        case_loaded = (
            (
                await session.execute(
                    select(Case).where(Case.id == case_id).order_by(Case.created_at)
                )
            )
            .scalar_one()
        )
        documents = (
            (
                await session.execute(
                    select(Document).where(Document.case_id == case_id).order_by(Document.id)
                )
            )
            .scalars()
            .all()
        )

        async def _fake_backfill(_db, *, document_id):
            assert document_id == miss_id
            doc = await session.get(Document, miss_id)
            doc.parsed_text = {
                "text": "back-filled evidence",
                "pages": [
                    {"page_number": 1, "text": "back-filled evidence", "tables": []}
                ],
                "tables": [],
            }
            await session.commit()

        with patch(
            "src.api.routes.cases.parse_and_persist_document",
            new=AsyncMock(side_effect=_fake_backfill),
        ) as parse_mock:
            entries = await _hydrate_raw_documents(session, documents)

        assert parse_mock.await_count == 1

        by_id = {entry["document_id"]: entry for entry in entries}
        assert by_id[str(cached_id)]["parsed_text"] == "cached evidence"
        assert by_id[str(miss_id)]["parsed_text"] == "back-filled evidence"

    # Confirm the back-fill landed in the real DB so subsequent runs
    # hit the cache.
    async with async_session() as session:
        reloaded = await session.get(Document, miss_id)
        assert reloaded.parsed_text == {
            "text": "back-filled evidence",
            "pages": [
                {"page_number": 1, "text": "back-filled evidence", "tables": []}
            ],
            "tables": [],
        }

    # Cleanup: the case + documents + outbox rows are left behind for
    # operator inspection; rely on the test DB being a throwaway. If the
    # suite grows we'll add a fixture-scoped cleanup.
    _ = case_loaded
