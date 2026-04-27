"""Sprint 3 3.B.3 — audit middleware persists source_ids.

When the wrapped search tools return a `ToolMessage` whose `artifact` is a
list of `Document` objects (3.B.1, 3.B.2), the audit middleware must
extract every `source_id` from `Document.metadata` and stash the list in
the audit row's `output_payload` (under `source_ids`). 4.C4.1 will later
promote this to a dedicated `retrieved_source_ids` column.

Hermetic: the DB writer (`append_audit_entry`) is monkeypatched; we only
verify the middleware contract.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document
from langchain_core.messages import ToolMessage

pytestmark = pytest.mark.asyncio


def _fake_state() -> dict:
    return {"case_id": "case-1", "agent_name": "legal-knowledge", "messages": []}


def _fake_request(tool_name: str = "search_precedents") -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={
            "name": tool_name,
            "args": {"query": "foo"},
            "id": "tc-1",
            "type": "tool_call",
        },
        tool=None,
        state=_fake_state(),
        runtime=None,
    )


async def test_source_ids_extracted_from_tool_message_artifact(monkeypatch):
    from src.pipeline.graph.middleware import audit as audit_mw

    captured: list[dict] = []

    async def _capture(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(audit_mw, "append_audit_entry", _capture)

    artifact = [
        Document(page_content="a", metadata={"source_id": "file-1:abcdef012345"}),
        Document(page_content="b", metadata={"source_id": "file-2:fedcba543210"}),
    ]
    handler = AsyncMock(
        return_value=ToolMessage(content="formatted", tool_call_id="tc-1", artifact=artifact)
    )

    await audit_mw.audit_tool_call.awrap_tool_call(_fake_request(), handler)

    assert len(captured) == 1
    output_payload = captured[0]["output_payload"]
    assert output_payload["source_ids"] == [
        "file-1:abcdef012345",
        "file-2:fedcba543210",
    ]


async def test_empty_artifact_yields_empty_source_ids(monkeypatch):
    from src.pipeline.graph.middleware import audit as audit_mw

    captured: list[dict] = []

    async def _capture(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(audit_mw, "append_audit_entry", _capture)

    handler = AsyncMock(return_value=ToolMessage(content="empty", tool_call_id="tc-1", artifact=[]))

    await audit_mw.audit_tool_call.awrap_tool_call(_fake_request(), handler)

    assert captured[0]["output_payload"]["source_ids"] == []


async def test_missing_artifact_yields_empty_source_ids(monkeypatch):
    """Tools that don't use content_and_artifact return ToolMessage with artifact=None."""
    from src.pipeline.graph.middleware import audit as audit_mw

    captured: list[dict] = []

    async def _capture(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(audit_mw, "append_audit_entry", _capture)

    handler = AsyncMock(return_value=ToolMessage(content="plain", tool_call_id="tc-1"))

    await audit_mw.audit_tool_call.awrap_tool_call(_fake_request(), handler)

    assert captured[0]["output_payload"]["source_ids"] == []


async def test_documents_without_source_id_are_skipped(monkeypatch):
    """Defensive: a Document whose metadata lacks source_id must not crash the writer."""
    from src.pipeline.graph.middleware import audit as audit_mw

    captured: list[dict] = []

    async def _capture(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(audit_mw, "append_audit_entry", _capture)

    artifact = [
        Document(page_content="a", metadata={"source_id": "file-1:abcdef012345"}),
        Document(page_content="b", metadata={"file_id": "no-source-id"}),
    ]
    handler = AsyncMock(
        return_value=ToolMessage(content="x", tool_call_id="tc-1", artifact=artifact)
    )

    await audit_mw.audit_tool_call.awrap_tool_call(_fake_request(), handler)

    assert captured[0]["output_payload"]["source_ids"] == ["file-1:abcdef012345"]
