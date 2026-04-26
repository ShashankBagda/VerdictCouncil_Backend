"""Regression: every tool-output Pydantic schema bound by the agent factory
must produce a JSON Schema OpenAI strict mode accepts.

Strict mode rejects:
  * an `array` without `items` (Pydantic emits this for `tuple[...]` types
    via `prefixItems`).
  * an object that allows `additionalProperties: true` (Pydantic emits this
    when `model_config` does not set `extra="forbid"`).

This test walks the resolved schema for every class registered in
`PHASE_SCHEMAS` and `RESEARCH_SCHEMAS`. A new tuple field, a missed
`extra="forbid"`, or a `set[...]` field will fail this guard before it
reaches OpenAI as a 400.
"""

from __future__ import annotations

import pytest

from src.pipeline.graph.agents.factory import PHASE_SCHEMAS, RESEARCH_SCHEMAS

ALL_SCHEMAS = {**PHASE_SCHEMAS, **RESEARCH_SCHEMAS}


def _walk(node, path: str = ""):
    if isinstance(node, dict):
        if node.get("type") == "array" and "items" not in node:
            yield path or "/", "array missing items"
        if (
            node.get("type") == "object"
            and node.get("additionalProperties") is True
        ):
            yield path or "/", "additionalProperties is true"
        for k, v in node.items():
            yield from _walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, x in enumerate(node):
            yield from _walk(x, f"{path}[{i}]")


@pytest.mark.parametrize("name,cls", sorted(ALL_SCHEMAS.items()))
def test_schema_is_openai_strict_safe(name: str, cls: type) -> None:
    issues = list(_walk(cls.model_json_schema()))
    assert not issues, (
        f"{cls.__name__} ({name}) emits OpenAI-strict-incompatible schema:\n"
        + "\n".join(f"  {p}: {why}" for p, why in issues)
    )
