"""Unit tests for domain vector store provisioning in src.services.knowledge_base."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services import knowledge_base as kb


def _mock_client(store_id: str = "vs_new_store") -> MagicMock:
    mock_store = MagicMock(id=store_id)
    client = MagicMock()
    client.vector_stores = MagicMock(create=AsyncMock(return_value=mock_store))
    return client


# ---------------------------------------------------------------------------
# create_domain_vector_store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_domain_vector_store_returns_store_id():
    """create_domain_vector_store calls OpenAI and returns the new store ID."""
    mock_client = _mock_client("vs_domain_abc")
    with patch("src.services.knowledge_base._get_client", return_value=mock_client):
        result = await kb.create_domain_vector_store("small_claims")

    assert result == "vs_domain_abc"
    mock_client.vector_stores.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_domain_vector_store_sets_metadata():
    """The created store includes domain_code and app metadata for traceability."""
    mock_client = _mock_client("vs_meta_check")
    with patch("src.services.knowledge_base._get_client", return_value=mock_client):
        await kb.create_domain_vector_store("traffic_violation")

    call_kwargs = mock_client.vector_stores.create.await_args.kwargs
    assert call_kwargs["metadata"]["domain_code"] == "traffic_violation"
    assert call_kwargs["metadata"]["app"] == "verdictcouncil"


@pytest.mark.asyncio
async def test_create_domain_vector_store_includes_domain_in_name():
    """Store name contains the domain code for operational discoverability."""
    mock_client = _mock_client("vs_name_check")
    with patch("src.services.knowledge_base._get_client", return_value=mock_client):
        await kb.create_domain_vector_store("small_claims")

    call_kwargs = mock_client.vector_stores.create.await_args.kwargs
    assert "small_claims" in call_kwargs["name"]


# ---------------------------------------------------------------------------
# ensure_domain_vector_store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_domain_vector_store_returns_existing_id():
    """If the domain already has a vector_store_id, it is returned without creating a new one."""
    domain_id = str(uuid.uuid4())
    domain = MagicMock()
    domain.vector_store_id = "vs_already_set"
    domain.is_active = True

    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=domain))
    )
    session.flush = AsyncMock()

    result_id, created = await kb.ensure_domain_vector_store(session, domain_id)

    assert result_id == "vs_already_set"
    assert created is False


@pytest.mark.asyncio
async def test_ensure_domain_vector_store_provisions_new_store():
    """If no vector_store_id exists, a new store is created and persisted."""
    domain_id = str(uuid.uuid4())
    domain = MagicMock()
    domain.code = "small_claims"
    domain.vector_store_id = None
    domain.is_active = False

    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=domain))
    )
    session.flush = AsyncMock()

    mock_client = _mock_client("vs_freshly_created")
    with patch("src.services.knowledge_base._get_client", return_value=mock_client):
        result_id, created = await kb.ensure_domain_vector_store(session, domain_id)

    assert result_id == "vs_freshly_created"
    assert created is True
    assert domain.vector_store_id == "vs_freshly_created"
    assert domain.is_active is True


@pytest.mark.asyncio
async def test_ensure_domain_vector_store_raises_on_missing_domain():
    """LookupError is raised when the domain row does not exist."""
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )

    with pytest.raises(LookupError, match="not found"):
        await kb.ensure_domain_vector_store(session, str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_ensure_domain_vector_store_activates_inactive_provisioned_domain():
    """Domain with a store id but is_active=False is re-activated without creating a new store."""
    domain_id = str(uuid.uuid4())
    domain = MagicMock()
    domain.vector_store_id = "vs_inactive_domain"
    domain.is_active = False

    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=domain))
    )
    session.flush = AsyncMock()

    result_id, created = await kb.ensure_domain_vector_store(session, domain_id)

    assert result_id == "vs_inactive_domain"
    assert created is False
    assert domain.is_active is True
