"""Sprint 3 3.B.6 — per-tenant vector store access isolation.

OpenAI vector stores are per-judge (`vc-judge-{judge_id}` per
`services/knowledge_base.py:28`). The route layer reads the store id
from `current_user.knowledge_base_vector_store_id`, so a request
authenticated as Judge B must never reach into Judge A's store.

Regression guard: even when both users exist with distinct store ids,
every KB route call routes to the *requesting* judge's store and never
to the other's.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_db
from src.models.user import User, UserRole

pytestmark = pytest.mark.asyncio


def _make_judge(store_id: str | None) -> MagicMock:
    user = MagicMock(spec=User)
    for k, v in {
        "id": uuid.uuid4(),
        "name": "Judge",
        "email": f"judge-{store_id}@example.com",
        "role": UserRole.judge,
        "password_hash": "hashed",
        "knowledge_base_vector_store_id": store_id,
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }.items():
        setattr(user, k, v)
    return user


def _app_for(user) -> object:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: user
    return app


JUDGE_A_STORE = "vs_judge_a"
JUDGE_B_STORE = "vs_judge_b"


async def test_list_uses_only_requesting_judges_store():
    judge_b = _make_judge(JUDGE_B_STORE)
    app = _app_for(judge_b)

    list_mock = AsyncMock(return_value=[])
    with patch("src.api.routes.knowledge_base.kb_service.list_kb_files", list_mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.get("/api/v1/knowledge-base/documents")

    assert resp.status_code == 200
    list_mock.assert_awaited_once_with(JUDGE_B_STORE)
    # Hard guarantee: judge A's store is never seen by judge B's request.
    for call in list_mock.await_args_list:
        assert JUDGE_A_STORE not in call.args
        assert JUDGE_A_STORE not in call.kwargs.values()


async def test_delete_routes_to_requesting_judges_store():
    judge_b = _make_judge(JUDGE_B_STORE)
    app = _app_for(judge_b)

    delete_mock = AsyncMock(return_value=True)
    with patch("src.api.routes.knowledge_base.kb_service.delete_kb_file", delete_mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.delete("/api/v1/knowledge-base/documents/file-xyz")

    assert resp.status_code in {200, 204}
    delete_mock.assert_awaited_once_with(JUDGE_B_STORE, "file-xyz")
    for call in delete_mock.await_args_list:
        assert JUDGE_A_STORE not in call.args


async def test_uninitialized_judge_cannot_access_anything():
    """A judge with no provisioned store cannot reach into another's by accident."""
    judge_no_store = _make_judge(None)
    app = _app_for(judge_no_store)

    list_mock = AsyncMock(return_value=[])
    with patch("src.api.routes.knowledge_base.kb_service.list_kb_files", list_mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            resp = await ac.get("/api/v1/knowledge-base/documents")

    # The route returns an empty result, NOT a fall-through to any other store.
    assert resp.status_code == 200
    list_mock.assert_not_awaited()


async def test_judge_a_and_judge_b_never_share_store_id():
    """Two consecutive requests authenticated as different judges must
    each see only their own store id — no leakage via dependency overrides
    or shared state.
    """
    list_mock = AsyncMock(return_value=[])

    judge_a = _make_judge(JUDGE_A_STORE)
    app_a = _app_for(judge_a)
    with patch("src.api.routes.knowledge_base.kb_service.list_kb_files", list_mock):
        async with AsyncClient(transport=ASGITransport(app=app_a), base_url="http://t") as ac:
            await ac.get("/api/v1/knowledge-base/documents")

    judge_b = _make_judge(JUDGE_B_STORE)
    app_b = _app_for(judge_b)
    with patch("src.api.routes.knowledge_base.kb_service.list_kb_files", list_mock):
        async with AsyncClient(transport=ASGITransport(app=app_b), base_url="http://t") as ac:
            await ac.get("/api/v1/knowledge-base/documents")

    seen = [c.args[0] for c in list_mock.await_args_list]
    assert seen == [JUDGE_A_STORE, JUDGE_B_STORE]
