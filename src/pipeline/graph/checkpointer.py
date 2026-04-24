"""LangGraph AsyncPostgresSaver factory for graph-level replay checkpoints.

This is separate from the domain `pipeline_checkpoints` table written by
`persist_case_state()`. These checkpoints are LangGraph's internal graph
state snapshots for replay/resume — not frontend-facing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.shared.config import settings

if TYPE_CHECKING:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

logger = logging.getLogger(__name__)

_checkpointer: "AsyncPostgresSaver | None" = None


async def get_checkpointer() -> "AsyncPostgresSaver":
    """Return the singleton AsyncPostgresSaver, creating it on first call."""
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    # Convert SQLAlchemy async URL to sync psycopg3 URI
    conn_str = settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    ).replace("postgresql+psycopg://", "postgresql://")

    _checkpointer = await AsyncPostgresSaver.afrom_conn_string(conn_str)
    await _checkpointer.setup()
    logger.info("LangGraph AsyncPostgresSaver initialised")
    return _checkpointer
