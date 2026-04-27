"""GET /api/v1/pipeline/manifest contract test.

The manifest is the canonical agent topology the frontend consumes
instead of hardcoding the list. This test pins the public shape so
a careless rename in `src/pipeline/manifest.py` immediately fails
CI rather than silently breaking the Building / Graph Mesh views.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_manifest_returns_seven_langgraph_agents() -> None:
    async with _client() as c:
        r = await c.get("/api/v1/pipeline/manifest")

    assert r.status_code == 200
    body = r.json()

    agent_ids = [a["id"] for a in body["agents"]]
    assert agent_ids == [
        "intake",
        "research-evidence",
        "research-facts",
        "research-witnesses",
        "research-law",
        "synthesis",
        "audit",
    ]

    # Each agent carries label + gate + layer.
    for agent in body["agents"]:
        assert {"id", "label", "gate", "layer"} <= set(agent)

    # Gates list has 4 entries with the layer labels the UI renders.
    gate_ids = [g["id"] for g in body["gates"]]
    assert gate_ids == ["gate1", "gate2", "gate3", "gate4"]

    # Topology fans out from intake to 4 research subagents and back in.
    edges = [(e["source"], e["target"]) for e in body["edges"]]
    assert ("intake", "research-evidence") in edges
    assert ("research-law", "synthesis") in edges
    assert ("synthesis", "audit") in edges
    assert len(edges) == 9  # 4 fan-out + 4 fan-in + 1 final

    # Legacy alias map collapses the old 9 names onto the 7 LangGraph IDs.
    aliases = body["legacy_alias_map"]
    assert aliases["case-processing"] == "intake"
    assert aliases["evidence-analysis"] == "research-evidence"
    assert aliases["hearing-governance"] == "audit"


@pytest.mark.asyncio
async def test_manifest_endpoint_does_not_require_auth() -> None:
    """The manifest is static topology metadata; no auth gate."""
    async with _client() as c:
        r = await c.get("/api/v1/pipeline/manifest")
    assert r.status_code == 200
