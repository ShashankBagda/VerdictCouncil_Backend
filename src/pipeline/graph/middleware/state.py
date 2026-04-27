"""State schema extension that lets middleware see `case_id` + `agent_name`.

LangChain's default `AgentState` only carries `messages`. The phase factory
(Sprint 1 1.A1.4) seeds these fields when invoking each phase agent so the
sse / cancel / audit hooks can attribute their telemetry to the right case
and agent without closure-binding per invocation.
"""

from __future__ import annotations

from langchain.agents.middleware.types import AgentState


class CaseAwareState(AgentState):
    """Agent state with VerdictCouncil case + phase identifiers.

    `case_id` is the UUID string of the case being processed.
    `agent_name` is the phase / subagent identifier (e.g. ``"intake"`` or
    ``"research-evidence"``).
    """

    case_id: str
    agent_name: str
