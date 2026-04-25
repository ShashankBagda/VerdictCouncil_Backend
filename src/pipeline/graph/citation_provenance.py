"""Shared citation-provenance utilities (Sprint 3 Workstream B).

The audit middleware (3.B.3), the agent factory (3.B.5 wiring), and
:func:`research_join_node` all need to walk LangChain ``ToolMessage``
artifacts and pull out the ``source_id`` metadata that
:mod:`src.pipeline.graph.tools` stamps onto each ``Document``.

This module owns the single canonical extractor. Two surfaces exist:

- :func:`source_ids_from_artifact` — pull ids from one
  ``ToolMessage.artifact`` (a list of ``Document``).
- :func:`source_ids_from_messages` — pull ids across an entire message
  chain, deduping while preserving order.

Both fail closed: any missing/None artifact yields an empty list rather
than raising, so the audit writer's fire-and-forget invariant holds.
"""

from __future__ import annotations

from typing import Any


def source_ids_from_artifact(artifact: Any) -> list[str]:
    """Return every ``source_id`` from a ``ToolMessage.artifact`` list."""
    if not artifact:
        return []
    out: list[str] = []
    for doc in artifact:
        meta = getattr(doc, "metadata", None) or {}
        sid = meta.get("source_id")
        if sid:
            out.append(str(sid))
    return out


def source_ids_from_messages(messages: list[Any]) -> list[str]:
    """Return ``source_id``s from every message's artifact, deduped + ordered."""
    seen: set[str] = set()
    out: list[str] = []
    for msg in messages:
        for sid in source_ids_from_artifact(getattr(msg, "artifact", None)):
            if sid not in seen:
                seen.add(sid)
                out.append(sid)
    return out
