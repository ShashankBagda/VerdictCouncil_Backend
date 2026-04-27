"""LangGraph checkpointer wiring for VerdictCouncil.

A single `AsyncPostgresSaver` instance lives for the full process lifetime
(FastAPI lifespan or arq worker startup) and is exposed via this module's
singleton getter. `build_graph()` consults it when no explicit checkpointer
is passed.

Tests pass an `InMemorySaver` directly to `build_graph(checkpointer=...)` and
do not touch the singleton.

Production wiring (per source-driven audit F-1/F-1b — async backend):
    async with AsyncPostgresSaver.from_conn_string(database_url) as saver:
        await saver.setup()
        set_checkpointer(saver)
        try:
            yield
        finally:
            set_checkpointer(None)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

_checkpointer: BaseCheckpointSaver | None = None


def set_checkpointer(saver: BaseCheckpointSaver | None) -> None:
    """Install (or clear) the process-wide checkpointer."""
    global _checkpointer
    _checkpointer = saver


def get_checkpointer() -> BaseCheckpointSaver | None:
    """Return the currently-installed checkpointer, if any."""
    return _checkpointer


@asynccontextmanager
async def lifespan_checkpointer(database_url: str) -> AsyncIterator[BaseCheckpointSaver]:
    """Async context manager that owns the AsyncPostgresSaver for app lifetime.

    Use from FastAPI lifespan and arq `on_startup` so the saver's
    underlying connection pool is opened once, `.setup()` runs idempotently,
    and the saver is torn down cleanly on shutdown.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(database_url) as saver:
        await saver.setup()
        set_checkpointer(saver)
        try:
            yield saver
        finally:
            set_checkpointer(None)
