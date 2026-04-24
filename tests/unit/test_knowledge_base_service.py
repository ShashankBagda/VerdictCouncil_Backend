"""Unit tests for src/services/knowledge_base.py — per-judge vector store wrappers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.user import User, UserRole
from src.services import knowledge_base as kb


def _make_user(**overrides) -> MagicMock:
    defaults = {
        "id": uuid.uuid4(),
        "name": "Justice Bao",
        "email": "bao@example.com",
        "role": UserRole.judge,
        "password_hash": "hashed",
        "knowledge_base_vector_store_id": None,
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }
    defaults.update(overrides)
    user = MagicMock(spec=User)
    for k, v in defaults.items():
        setattr(user, k, v)
    return user


# ---------------------------------------------------------------------------
# create_judge_vector_store
# ---------------------------------------------------------------------------


async def test_create_judge_vector_store_returns_store_id():
    mock_store = MagicMock(id="vs_judge_abc")
    mock_client = MagicMock(vector_stores=MagicMock(create=AsyncMock(return_value=mock_store)))
    with patch("src.services.knowledge_base._get_client", return_value=mock_client):
        result = await kb.create_judge_vector_store("judge-123")

    assert result == "vs_judge_abc"
    mock_client.vector_stores.create.assert_awaited_once()
    call_kwargs = mock_client.vector_stores.create.await_args.kwargs
    assert call_kwargs["name"] == "vc-judge-judge-123"
    assert call_kwargs["metadata"]["judge_id"] == "judge-123"


# ---------------------------------------------------------------------------
# upload_document_to_kb
# ---------------------------------------------------------------------------


async def test_upload_document_to_kb_creates_file_and_attaches():
    file_obj = MagicMock(id="file-xyz")
    vs_file = MagicMock(id="file-xyz", status="completed")
    mock_client = MagicMock()
    mock_client.files = MagicMock(create=AsyncMock(return_value=file_obj))
    mock_client.vector_stores = MagicMock(files=MagicMock(create_and_poll=AsyncMock(return_value=vs_file)))

    with patch("src.services.knowledge_base._get_client", return_value=mock_client):
        result = await kb.upload_document_to_kb(
            vector_store_id="vs_judge_abc",
            file_bytes=b"hello",
            filename="brief.pdf",
        )

    assert result["file_id"] == "file-xyz"
    assert result["filename"] == "brief.pdf"
    assert result["status"] == "completed"
    assert result["bytes"] == 5

    mock_client.files.create.assert_awaited_once()
    mock_client.vector_stores.files.create_and_poll.assert_awaited_once_with(
        vector_store_id="vs_judge_abc",
        file_id="file-xyz",
    )


# ---------------------------------------------------------------------------
# search_kb
# ---------------------------------------------------------------------------


async def test_search_kb_returns_shaped_hits():
    hit1 = MagicMock(
        file_id="file-a",
        filename="brief.pdf",
        content=[MagicMock(text="matching snippet")],
        score=0.91,
    )
    hit2 = MagicMock(
        file_id="file-b",
        filename="memo.pdf",
        content=[],
        score=0.42,
    )
    search_result = MagicMock(data=[hit1, hit2])
    mock_client = MagicMock(vector_stores=MagicMock(search=AsyncMock(return_value=search_result)))

    with patch("src.services.knowledge_base._get_client", return_value=mock_client):
        results = await kb.search_kb(
            vector_store_id="vs_judge_abc",
            query="fair use",
            max_results=2,
        )

    assert len(results) == 2
    assert results[0] == {
        "file_id": "file-a",
        "filename": "brief.pdf",
        "content": "matching snippet",
        "score": 0.91,
    }
    assert results[1]["content"] == ""
    mock_client.vector_stores.search.assert_awaited_once_with(
        vector_store_id="vs_judge_abc",
        query="fair use",
        max_num_results=2,
    )


# ---------------------------------------------------------------------------
# list_kb_files
# ---------------------------------------------------------------------------


class _FakeAsyncPaginator:
    """Async iterable stand-in for openai.AsyncPaginator.

    The real paginator is awaitable (yielding the first page) and async-iterable
    (yielding every item across pages). Our wrapper uses ``async for`` on the
    awaited result, so this stand-in only needs the async-iterator protocol.
    """

    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        self._idx = 0
        return self

    async def __anext__(self):
        if self._idx >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._idx]
        self._idx += 1
        return item


async def test_list_kb_files_enriches_each_file_with_metadata():
    vs_file = MagicMock(id="file-xyz", status="completed")
    file_obj = MagicMock(filename="brief.pdf", bytes=1024, created_at=1700000000)

    mock_client = MagicMock()
    mock_client.vector_stores = MagicMock(files=MagicMock(list=AsyncMock(return_value=_FakeAsyncPaginator([vs_file]))))
    mock_client.files = MagicMock(retrieve=AsyncMock(return_value=file_obj))

    with patch("src.services.knowledge_base._get_client", return_value=mock_client):
        result = await kb.list_kb_files("vs_judge_abc")

    assert len(result) == 1
    assert result[0] == {
        "file_id": "file-xyz",
        "filename": "brief.pdf",
        "status": "completed",
        "bytes": 1024,
        "created_at": 1700000000,
    }
    mock_client.files.retrieve.assert_awaited_once_with("file-xyz")


async def test_list_kb_files_walks_all_pages():
    # Regression: naked ``await .list(...)`` returns only the first page (default 20).
    # The wrapper must iterate the AsyncPaginator across every page.
    vs_files = [MagicMock(id=f"file-{i}", status="completed") for i in range(25)]
    file_objs = {f"file-{i}": MagicMock(filename=f"doc-{i}.pdf", bytes=100, created_at=1700000000) for i in range(25)}

    mock_client = MagicMock()
    mock_client.vector_stores = MagicMock(files=MagicMock(list=AsyncMock(return_value=_FakeAsyncPaginator(vs_files))))
    mock_client.files = MagicMock(retrieve=AsyncMock(side_effect=lambda fid: file_objs[fid]))

    with patch("src.services.knowledge_base._get_client", return_value=mock_client):
        result = await kb.list_kb_files("vs_judge_abc")

    assert len(result) == 25
    assert {r["file_id"] for r in result} == {f"file-{i}" for i in range(25)}


async def test_list_kb_files_propagates_metadata_failure():
    # Contract: if files.retrieve fails we surface the error so the route layer
    # returns 503 — fabricating "filename: unknown" would hide a real outage.
    vs_file = MagicMock(id="file-xyz", status="completed")
    mock_client = MagicMock()
    mock_client.vector_stores = MagicMock(files=MagicMock(list=AsyncMock(return_value=_FakeAsyncPaginator([vs_file]))))
    mock_client.files = MagicMock(retrieve=AsyncMock(side_effect=RuntimeError("file gone")))

    with (
        patch("src.services.knowledge_base._get_client", return_value=mock_client),
        pytest.raises(RuntimeError, match="file gone"),
    ):
        await kb.list_kb_files("vs_judge_abc")


# ---------------------------------------------------------------------------
# delete_kb_file
# ---------------------------------------------------------------------------


async def test_delete_kb_file_detaches_and_deletes_raw_file():
    mock_client = MagicMock()
    mock_client.vector_stores = MagicMock(files=MagicMock(delete=AsyncMock(return_value=MagicMock())))
    mock_client.files = MagicMock(delete=AsyncMock(return_value=MagicMock()))

    with patch("src.services.knowledge_base._get_client", return_value=mock_client):
        result = await kb.delete_kb_file("vs_judge_abc", "file-xyz")

    assert result is True
    mock_client.vector_stores.files.delete.assert_awaited_once_with(
        vector_store_id="vs_judge_abc",
        file_id="file-xyz",
    )
    mock_client.files.delete.assert_awaited_once_with("file-xyz")


async def test_delete_kb_file_propagates_raw_delete_error():
    # Contract: if the raw files.delete fails we surface the error so the
    # route returns 503 — silently swallowing it leaves an orphan file eating
    # OpenAI quota while the API reports deleted=true.
    mock_client = MagicMock()
    mock_client.vector_stores = MagicMock(files=MagicMock(delete=AsyncMock(return_value=MagicMock())))
    mock_client.files = MagicMock(delete=AsyncMock(side_effect=RuntimeError("upstream 500")))

    with (
        patch("src.services.knowledge_base._get_client", return_value=mock_client),
        pytest.raises(RuntimeError, match="upstream 500"),
    ):
        await kb.delete_kb_file("vs_judge_abc", "file-xyz")


# ---------------------------------------------------------------------------
# ensure_judge_vector_store
# ---------------------------------------------------------------------------


async def test_ensure_judge_vector_store_creates_when_missing():
    user = _make_user(knowledge_base_vector_store_id=None)
    db = AsyncMock()
    locked_user = _make_user(id=user.id, knowledge_base_vector_store_id=None)
    exec_result = MagicMock()
    exec_result.scalar_one_or_none = MagicMock(return_value=locked_user)
    db.execute = AsyncMock(return_value=exec_result)
    db.flush = AsyncMock()

    with patch(
        "src.services.knowledge_base.create_judge_vector_store",
        AsyncMock(return_value="vs_new_store"),
    ) as create_mock:
        store_id, created = await kb.ensure_judge_vector_store(db, user)

    assert store_id == "vs_new_store"
    assert created is True
    assert locked_user.knowledge_base_vector_store_id == "vs_new_store"
    create_mock.assert_awaited_once_with(str(user.id))
    db.flush.assert_awaited_once()


async def test_ensure_judge_vector_store_returns_existing_without_creating():
    user = _make_user(knowledge_base_vector_store_id="vs_existing")
    db = AsyncMock()
    locked_user = _make_user(id=user.id, knowledge_base_vector_store_id="vs_existing")
    exec_result = MagicMock()
    exec_result.scalar_one_or_none = MagicMock(return_value=locked_user)
    db.execute = AsyncMock(return_value=exec_result)

    with patch(
        "src.services.knowledge_base.create_judge_vector_store",
        AsyncMock(),
    ) as create_mock:
        store_id, created = await kb.ensure_judge_vector_store(db, user)

    assert store_id == "vs_existing"
    assert created is False
    create_mock.assert_not_awaited()


async def test_ensure_judge_vector_store_uses_populate_existing_and_for_update():
    # Regression: without populate_existing, SQLAlchemy's identity map returns
    # the caller's cached User with stale knowledge_base_vector_store_id=None.
    # Two concurrent requests would both miss the existing store and provision
    # duplicate vector stores on OpenAI.
    user = _make_user(knowledge_base_vector_store_id=None)
    db = AsyncMock()
    # Simulate request A's commit landing while request B waited on the lock:
    # the re-read returns the already-populated store from the DB.
    fresh_locked_user = _make_user(id=user.id, knowledge_base_vector_store_id="vs_created_by_a")
    exec_result = MagicMock()
    exec_result.scalar_one_or_none = MagicMock(return_value=fresh_locked_user)
    db.execute = AsyncMock(return_value=exec_result)

    with patch(
        "src.services.knowledge_base.create_judge_vector_store",
        AsyncMock(),
    ) as create_mock:
        store_id, created = await kb.ensure_judge_vector_store(db, user)

    assert store_id == "vs_created_by_a"
    assert created is False
    create_mock.assert_not_awaited()

    # Inspect the SELECT statement: it must lock the row AND force a fresh read.
    select_stmt = db.execute.await_args.args[0]
    compiled = str(select_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "FOR UPDATE" in compiled
    assert select_stmt.get_execution_options().get("populate_existing") is True


async def test_ensure_judge_vector_store_raises_if_user_missing():
    user = _make_user()
    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=exec_result)

    with pytest.raises(LookupError):
        await kb.ensure_judge_vector_store(db, user)
