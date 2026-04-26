"""Q2.2 — runner-side hydration of `Document.parsed_text` into
`CaseState.raw_documents`.

Locks three behaviours:
- A document with `parsed_text` populated flows into `raw_documents[i]`
  with `parsed_text` (string) and `pages` (list).
- A document with NULL `parsed_text` triggers a runner-side back-fill
  through `parse_and_persist_document`; the result is then read off
  the (now-updated) Document and folded into the entry.
- A back-fill failure leaves the entry with `parsed_text=""` so the
  agent's tool-call fallback kicks in. The run does NOT halt.

Plus the `intake_phase_start` structured log line that operators use
to spot intake-stage halts.
"""

from __future__ import annotations

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.cases import _hydrate_raw_documents, _log_intake_phase_start


def _make_doc(*, parsed_text=None, pages=None, openai_file_id="file-abc"):
    doc = MagicMock()
    doc.id = uuid.uuid4()
    doc.filename = "doc.pdf"
    doc.file_type = "application/pdf"
    doc.openai_file_id = openai_file_id
    doc.parsed_text = parsed_text
    doc.pages = pages
    return doc


@pytest.mark.asyncio
async def test_hydrate_uses_cached_parsed_text_when_populated():
    """Cache hit — no parse_document call, entry carries the cached
    text + pages, parse service is not invoked."""
    doc = _make_doc(
        parsed_text={
            "text": "Notice of traffic offence …",
            "pages": [{"page_number": 1, "text": "Notice …", "tables": []}],
            "tables": [],
        }
    )
    db = MagicMock()

    with patch(
        "src.api.routes.cases.parse_and_persist_document",
        new=AsyncMock(),
    ) as parse_mock:
        entries = await _hydrate_raw_documents(db, [doc])

    parse_mock.assert_not_awaited()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["document_id"] == str(doc.id)
    assert entry["parsed_text"] == "Notice of traffic offence …"
    assert entry["pages"] == [{"page_number": 1, "text": "Notice …", "tables": []}]


@pytest.mark.asyncio
async def test_hydrate_backfills_when_parsed_text_null():
    """Cache miss — runner calls `parse_and_persist_document`; after
    the service returns the Document is updated in place (SQLAlchemy
    identity map), and the entry picks up the freshly-parsed text."""
    doc = _make_doc(parsed_text=None)
    db = MagicMock()

    async def _fake_backfill(_db, *, document_id):
        assert document_id == doc.id
        doc.parsed_text = {
            "text": "back-filled",
            "pages": [{"page_number": 1, "text": "back-filled", "tables": []}],
            "tables": [],
        }

    with patch(
        "src.api.routes.cases.parse_and_persist_document",
        new=AsyncMock(side_effect=_fake_backfill),
    ) as parse_mock:
        entries = await _hydrate_raw_documents(db, [doc])

    parse_mock.assert_awaited_once()
    assert entries[0]["parsed_text"] == "back-filled"
    assert entries[0]["pages"][0]["text"] == "back-filled"


@pytest.mark.asyncio
async def test_hydrate_falls_back_to_empty_string_on_backfill_failure(caplog):
    """`parse_and_persist_document` exception → entry gets parsed_text=""
    so the agent's tool-call fallback runs. The run does NOT halt."""
    doc = _make_doc(parsed_text=None)
    db = MagicMock()

    with patch(
        "src.api.routes.cases.parse_and_persist_document",
        new=AsyncMock(side_effect=RuntimeError("OpenAI 500")),
    ), caplog.at_level(logging.WARNING, logger="src.api.routes.cases"):
        entries = await _hydrate_raw_documents(db, [doc])

    assert entries[0]["parsed_text"] == ""
    assert any(
        "back-fill failed" in rec.message.lower()
        and str(doc.id) in rec.message
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_hydrate_falls_back_to_empty_when_service_silently_left_null():
    """The service's soft-fail path (DocumentParseError) returns cleanly
    but leaves parsed_text NULL. Runner still needs to give the entry
    an empty string so the contract is consistent."""
    doc = _make_doc(parsed_text=None)
    db = MagicMock()

    with patch(
        "src.api.routes.cases.parse_and_persist_document",
        new=AsyncMock(return_value=None),
    ):
        entries = await _hydrate_raw_documents(db, [doc])

    assert entries[0]["parsed_text"] == ""
    # `pages` falls back to legacy Document.pages (None) rather than
    # synthesising — the agent's tool fallback can fill the gap.
    assert entries[0]["pages"] is None


@pytest.mark.asyncio
async def test_hydrate_skips_backfill_when_no_openai_file_id():
    """No file_id means upload to OpenAI Files failed at upload time —
    the service would no-op anyway. Skip the call entirely; entry
    gets parsed_text=""."""
    doc = _make_doc(parsed_text=None, openai_file_id=None)
    db = MagicMock()

    with patch(
        "src.api.routes.cases.parse_and_persist_document",
        new=AsyncMock(),
    ) as parse_mock:
        entries = await _hydrate_raw_documents(db, [doc])

    parse_mock.assert_not_awaited()
    assert entries[0]["parsed_text"] == ""


@pytest.mark.asyncio
async def test_hydrate_prefers_parsed_text_pages_over_legacy_column():
    """Some legacy documents have `Document.pages` populated from a
    prior code path. When `parsed_text` is also populated, it wins —
    so the agent sees the freshest extraction and a single source of
    truth."""
    doc = _make_doc(
        parsed_text={
            "text": "fresh",
            "pages": [{"page_number": 1, "text": "fresh", "tables": []}],
            "tables": [],
        },
        pages=[{"page_number": 1, "text": "STALE", "tables": []}],
    )
    db = MagicMock()

    with patch(
        "src.api.routes.cases.parse_and_persist_document",
        new=AsyncMock(),
    ):
        entries = await _hydrate_raw_documents(db, [doc])

    assert entries[0]["pages"][0]["text"] == "fresh"


def test_intake_phase_start_log_line(caplog):
    """Operators key on the `intake_phase_start` line to spot cases
    that are entering the pipeline with empty parties / zero parsed
    text — both of which predict an intake halt."""
    case_id = uuid.uuid4()
    raw_documents = [
        {"document_id": "a", "parsed_text": "hello world"},
        {"document_id": "b", "parsed_text": ""},
    ]

    with caplog.at_level(logging.INFO, logger="src.api.routes.cases"):
        _log_intake_phase_start(case_id=case_id, raw_documents=raw_documents, parties_count=2)

    matched = [
        rec for rec in caplog.records if "intake_phase_start" in rec.message
    ]
    assert matched
    msg = matched[0].message
    assert f"case_id={case_id}" in msg
    assert "documents=2" in msg
    assert "parties=2" in msg
    assert "parsed_text_chars=11" in msg  # "hello world" = 11
