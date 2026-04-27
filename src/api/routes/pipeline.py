"""Pipeline manifest endpoint.

Exposes the canonical LangGraph runtime topology (agents, gates,
edges, legacy alias map) so frontend clients can render the agent
mesh without hardcoding the list. Mirrors what `pipeline.manifest`
serializes; cached at module import because the topology only
changes on backend deploy.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.pipeline.manifest import manifest_dict

router = APIRouter()

_MANIFEST_PAYLOAD = manifest_dict()


@router.get(
    "/manifest",
    operation_id="get_pipeline_manifest",
    summary="Get the LangGraph pipeline manifest",
    description=(
        "Returns the canonical agent topology — ordered agent IDs and "
        "labels, gate (layer) grouping, graph edges, and the "
        "legacy → LangGraph alias map. The frontend can fetch this once "
        "at app load instead of hardcoding the agent list."
    ),
)
async def get_pipeline_manifest() -> dict:
    return _MANIFEST_PAYLOAD
