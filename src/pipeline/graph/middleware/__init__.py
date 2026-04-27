"""LangChain middleware for the VerdictCouncil pipeline.

Four hooks (Sprint 1 1.A1.2):

- `sse_tool_emitter` — wraps every tool call so judges see `tool_call` /
  `tool_result` events as they happen (replaces the inline emissions in
  `nodes/common.py`).
- `token_usage_emitter` — wraps every model call so token spend lands on
  the SSE stream as a `token_usage` event.
- `cancel_check` — pre-model hook that consults the Redis cancel flag and
  emits `Command(goto="end")` when the judge has cancelled the run.
- `audit_tool_call` — wraps every tool call and records it to the audit
  log surface (Sprint 4 4.C4.2 fills in the persistence — for now this
  is a thin async helper that downstream tests stub).

The phase factory in 1.A1.4 attaches them to `create_agent(...)` as a
list. State extension lives in `state.py` so the hooks can read
`case_id` / `agent_name` off the agent state.
"""

from src.pipeline.graph.middleware.audit import audit_tool_call
from src.pipeline.graph.middleware.cancellation import cancel_check
from src.pipeline.graph.middleware.sse_bridge import (
    sse_tool_emitter,
    token_usage_emitter,
)
from src.pipeline.graph.middleware.state import CaseAwareState

__all__ = [
    "CaseAwareState",
    "audit_tool_call",
    "cancel_check",
    "sse_tool_emitter",
    "token_usage_emitter",
]
