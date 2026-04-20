"""Process-scoped wiring for MeshPipelineRunner + SolaceA2AClient.

One `SolaceA2AClient` per API process, opened lazily on first use and
closed from the FastAPI lifespan shutdown hook. Callers obtain a
runner via `get_mesh_runner()`; the underlying Solace session is
shared across concurrent pipeline runs.

This module intentionally knows nothing about OpenAI / Redis client
construction — `MeshPipelineRunner` already owns sensible defaults
using `src.shared.config.settings`.
"""

from __future__ import annotations

import asyncio
import logging

from src.pipeline._solace_a2a_client import SolaceA2AClient
from src.pipeline.mesh_runner import MeshPipelineRunner
from src.shared.config import settings

logger = logging.getLogger(__name__)


_client: SolaceA2AClient | None = None
_client_lock: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


async def get_a2a_client() -> SolaceA2AClient:
    """Return the process-wide SolaceA2AClient, connecting on first call."""
    global _client
    async with _lock():
        if _client is None:
            client = SolaceA2AClient(
                broker_url=settings.solace_broker_url,
                vpn_name=settings.solace_broker_vpn,
                username=settings.solace_broker_username,
                password=settings.solace_broker_password,
                namespace=settings.namespace,
            )
            await client.connect()
            _client = client
        return _client


async def get_mesh_runner() -> MeshPipelineRunner:
    """Return a MeshPipelineRunner bound to the shared Solace client."""
    client = await get_a2a_client()
    return MeshPipelineRunner(a2a_client=client, namespace=settings.namespace)


async def close_mesh_a2a_client() -> None:
    """Tear down the shared Solace session. Safe to call on shutdown."""
    global _client
    async with _lock():
        if _client is not None:
            try:
                await _client.close()
            except Exception as exc:
                logger.warning("Error closing shared SolaceA2AClient: %s", exc)
            _client = None
