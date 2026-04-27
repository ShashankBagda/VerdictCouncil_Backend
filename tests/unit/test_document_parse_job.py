"""Q2.1 — worker-side `parse_and_persist_document` happy-path + failure tests."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.document_parse import parse_and_persist_document
from src.tools.parse_document import DocumentParseError


def _make_db(document: MagicMock | None) -> MagicMock:
    db = MagicMock()
    db.get = AsyncMock(return_value=document)
    db.commit = AsyncMock()
    return db


def _make_doc(file_id: str | None = "file-abc123") -> MagicMock:
    doc = MagicMock()
    doc.id = uuid.uuid4()
    doc.openai_file_id = file_id
    doc.parsed_text = None
    return doc


@pytest.mark.asyncio
async def test_happy_path_writes_persisted_subset() -> None:
    """Successful parse stores only `text`/`pages`/`tables` — drops the
    non-JSON-safe `SanitizationResult`."""
    doc = _make_doc()
    db = _make_db(doc)

    parse_result = {
        "file_id": "file-abc123",
        "filename": "notice.pdf",
        "text": "Notice of traffic offence …",
        "pages": [{"page_number": 1, "text": "Notice …", "tables": []}],
        "tables": [],
        "sanitization": object(),  # not JSON-serialisable; must NOT be persisted
    }
    with patch(
        "src.services.document_parse.parse_document",
        new=AsyncMock(return_value=parse_result),
    ):
        await parse_and_persist_document(db, document_id=doc.id)

    assert doc.parsed_text == {
        "text": "Notice of traffic offence …",
        "pages": [{"page_number": 1, "text": "Notice …", "tables": []}],
        "tables": [],
    }
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_document_is_a_no_op() -> None:
    """Document deleted between enqueue and dispatch — log + return,
    don't crash."""
    db = _make_db(None)

    with patch("src.services.document_parse.parse_document", new=AsyncMock()) as parse_mock:
        await parse_and_persist_document(db, document_id=uuid.uuid4())

    parse_mock.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_openai_file_id_skips_parse() -> None:
    """OpenAI Files upload failed at upload time → no file_id, nothing to
    parse. Skip and let the runner-side fallback (Q2.2) handle it."""
    doc = _make_doc(file_id=None)
    db = _make_db(doc)

    with patch("src.services.document_parse.parse_document", new=AsyncMock()) as parse_mock:
        await parse_and_persist_document(db, document_id=doc.id)

    parse_mock.assert_not_awaited()
    assert doc.parsed_text is None
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_parse_failure_is_soft_and_leaves_column_null(caplog) -> None:
    """`DocumentParseError` is swallowed (one-shot semantics — no arq
    retry burning OpenAI calls). Column stays NULL; runner-side
    fallback (Q2.2) re-parses lazily on first pipeline use."""
    import logging

    doc = _make_doc()
    db = _make_db(doc)

    with patch(
        "src.services.document_parse.parse_document",
        new=AsyncMock(side_effect=DocumentParseError("OpenAI 500")),
    ), caplog.at_level(logging.WARNING, logger="src.services.document_parse"):
        await parse_and_persist_document(db, document_id=doc.id)

    assert doc.parsed_text is None
    db.commit.assert_not_awaited()
    assert any("OpenAI 500" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_unexpected_exception_propagates() -> None:
    """Programming bugs / DB outages still surface so the outbox marks
    the job failed and operators notice."""
    doc = _make_doc()
    db = _make_db(doc)

    with patch(
        "src.services.document_parse.parse_document",
        new=AsyncMock(side_effect=RuntimeError("disk full")),
    ), pytest.raises(RuntimeError):
        await parse_and_persist_document(db, document_id=doc.id)

    assert doc.parsed_text is None
