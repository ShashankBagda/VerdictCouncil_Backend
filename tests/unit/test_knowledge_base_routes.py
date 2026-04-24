"""Unit tests for per-judge knowledge base routes (initialize/upload/list/delete/search)."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_db
from src.models.user import User, UserRole


def _make_user(role: UserRole = UserRole.judge, **overrides) -> MagicMock:
    defaults = {
        "id": uuid.uuid4(),
        "name": "Justice Bao",
        "email": "bao@example.com",
        "role": role,
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


def _app_for(user, db=None):
    app = create_app()
    if db is None:
        db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return app


# ---------------------------------------------------------------------------
# POST /initialize
# ---------------------------------------------------------------------------


async def test_initialize_creates_store_and_returns_created_true():
    user = _make_user(knowledge_base_vector_store_id=None)
    app = _app_for(user)

    with patch(
        "src.api.routes.knowledge_base.kb_service.ensure_judge_vector_store",
        AsyncMock(return_value=("vs_new", True)),
    ) as ensure_mock:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.post("/api/v1/knowledge-base/initialize")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"vector_store_id": "vs_new", "created": True}
    ensure_mock.assert_awaited_once()


async def test_initialize_is_idempotent_returns_created_false():
    # Regression: `created` must come from the helper's post-lock view, not
    # from the pre-lock user object — otherwise the loser of a concurrent
    # initialize race would claim it created the store that the winner made.
    user = _make_user(knowledge_base_vector_store_id=None)
    app = _app_for(user)

    with patch(
        "src.api.routes.knowledge_base.kb_service.ensure_judge_vector_store",
        AsyncMock(return_value=("vs_existing", False)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.post("/api/v1/knowledge-base/initialize")

    assert resp.status_code == 200
    assert resp.json() == {"vector_store_id": "vs_existing", "created": False}


async def test_initialize_forbidden_for_non_judge():
    clerk = _make_user(role=UserRole.admin)
    app = _app_for(clerk)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post("/api/v1/knowledge-base/initialize")

    assert resp.status_code == 403


async def test_initialize_openai_failure_returns_503():
    user = _make_user()
    app = _app_for(user)

    with patch(
        "src.api.routes.knowledge_base.kb_service.ensure_judge_vector_store",
        AsyncMock(side_effect=Exception("openai down")),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.post("/api/v1/knowledge-base/initialize")

    assert resp.status_code == 503
    assert "detail" in resp.json()


# ---------------------------------------------------------------------------
# POST /documents (upload)
# ---------------------------------------------------------------------------


async def test_upload_document_happy_path():
    user = _make_user()
    app = _app_for(user)

    with (
        patch(
            "src.api.routes.knowledge_base.kb_service.ensure_judge_vector_store",
            AsyncMock(return_value=("vs_judge", False)),
        ),
        patch(
            "src.api.routes.knowledge_base.kb_service.upload_document_to_kb",
            AsyncMock(
                return_value={
                    "file_id": "file-xyz",
                    "filename": "brief.pdf",
                    "status": "completed",
                    "bytes": 5,
                }
            ),
        ) as upload_mock,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.post(
                "/api/v1/knowledge-base/documents",
                files={"file": ("brief.pdf", io.BytesIO(b"hello"), "application/pdf")},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"id": "file-xyz", "filename": "brief.pdf", "status": "completed"}
    upload_mock.assert_awaited_once()
    kwargs = upload_mock.await_args.kwargs
    assert kwargs["vector_store_id"] == "vs_judge"
    assert kwargs["file_bytes"] == b"hello"
    assert kwargs["filename"] == "brief.pdf"


async def test_upload_document_too_large_returns_413():
    user = _make_user()
    app = _app_for(user)
    # 30 MB — over the 25 MB default.
    big_bytes = b"x" * (30 * 1024 * 1024)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(
            "/api/v1/knowledge-base/documents",
            files={"file": ("big.pdf", io.BytesIO(big_bytes), "application/pdf")},
        )

    assert resp.status_code == 413
    assert "exceeds" in resp.json()["detail"].lower()


async def test_upload_document_forbidden_for_non_judge():
    clerk = _make_user(role=UserRole.admin)
    app = _app_for(clerk)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post(
            "/api/v1/knowledge-base/documents",
            files={"file": ("x.pdf", io.BytesIO(b"x"), "application/pdf")},
        )

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /documents
# ---------------------------------------------------------------------------


async def test_list_documents_returns_items_and_total():
    user = _make_user(knowledge_base_vector_store_id="vs_judge")
    app = _app_for(user)

    rows = [
        {
            "file_id": "file-a",
            "filename": "brief.pdf",
            "status": "completed",
            "bytes": 1024,
            "created_at": 1700000000,
        },
        {
            "file_id": "file-b",
            "filename": "memo.pdf",
            "status": "completed",
            "bytes": 2048,
            "created_at": 1700000100,
        },
    ]

    with patch(
        "src.api.routes.knowledge_base.kb_service.list_kb_files",
        AsyncMock(return_value=rows),
    ) as list_mock:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.get("/api/v1/knowledge-base/documents")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["items"][0]["id"] == "file-a"
    assert body["items"][0]["filename"] == "brief.pdf"
    list_mock.assert_awaited_once_with("vs_judge")


async def test_list_documents_empty_when_uninitialized():
    user = _make_user(knowledge_base_vector_store_id=None)
    app = _app_for(user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.get("/api/v1/knowledge-base/documents")

    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


async def test_list_documents_openai_failure_returns_503():
    user = _make_user(knowledge_base_vector_store_id="vs_judge")
    app = _app_for(user)

    with patch(
        "src.api.routes.knowledge_base.kb_service.list_kb_files",
        AsyncMock(side_effect=Exception("openai down")),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.get("/api/v1/knowledge-base/documents")

    assert resp.status_code == 503


async def test_delete_document_openai_failure_returns_503():
    user = _make_user(knowledge_base_vector_store_id="vs_judge")
    app = _app_for(user)

    with patch(
        "src.api.routes.knowledge_base.kb_service.delete_kb_file",
        AsyncMock(side_effect=Exception("openai down")),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.delete("/api/v1/knowledge-base/documents/file-xyz")

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# DELETE /documents/{file_id}
# ---------------------------------------------------------------------------


async def test_delete_document_round_trips_file_id():
    user = _make_user(knowledge_base_vector_store_id="vs_judge")
    app = _app_for(user)

    with patch(
        "src.api.routes.knowledge_base.kb_service.delete_kb_file",
        AsyncMock(return_value=True),
    ) as delete_mock:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.delete("/api/v1/knowledge-base/documents/file-xyz")

    assert resp.status_code == 200
    assert resp.json() == {"id": "file-xyz", "deleted": True}
    delete_mock.assert_awaited_once_with("vs_judge", "file-xyz")


async def test_delete_document_404_when_uninitialized():
    user = _make_user(knowledge_base_vector_store_id=None)
    app = _app_for(user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.delete("/api/v1/knowledge-base/documents/file-xyz")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /search
# ---------------------------------------------------------------------------


async def test_search_returns_hits():
    user = _make_user(knowledge_base_vector_store_id="vs_judge")
    app = _app_for(user)

    hits = [
        {"file_id": "file-a", "filename": "brief.pdf", "content": "snippet", "score": 0.9},
    ]

    with patch(
        "src.api.routes.knowledge_base.kb_service.search_kb",
        AsyncMock(return_value=hits),
    ) as search_mock:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.post(
                "/api/v1/knowledge-base/search",
                json={"q": "fair use", "limit": 3},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["file_id"] == "file-a"
    assert body["items"][0]["score"] == 0.9
    search_mock.assert_awaited_once_with(
        vector_store_id="vs_judge", query="fair use", max_results=3
    )


async def test_search_404_when_uninitialized():
    user = _make_user(knowledge_base_vector_store_id=None)
    app = _app_for(user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post("/api/v1/knowledge-base/search", json={"q": "anything"})

    assert resp.status_code == 404


async def test_search_validation_requires_q():
    user = _make_user(knowledge_base_vector_store_id="vs_judge")
    app = _app_for(user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        resp = await ac.post("/api/v1/knowledge-base/search", json={})

    assert resp.status_code == 422


async def test_search_openai_failure_returns_503():
    user = _make_user(knowledge_base_vector_store_id="vs_judge")
    app = _app_for(user)

    with patch(
        "src.api.routes.knowledge_base.kb_service.search_kb",
        AsyncMock(side_effect=Exception("openai down")),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.post(
                "/api/v1/knowledge-base/search",
                json={"q": "fair use"},
            )

    assert resp.status_code == 503
