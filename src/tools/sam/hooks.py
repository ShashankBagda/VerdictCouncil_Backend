"""ADK before_tool_callback — hoists payload state into tool_context.state.

IMPORTANT SAM CONSTRAINT (verified against solace_agent_mesh source):

1. `agent.before_tool_callback` is hardcoded in setup.py — it cannot be
   overridden or supplemented via YAML config.  This function must be
   registered programmatically by any code that sets up the ADK agent.

2. `tool_context.state["a2a_context"]` exists but does NOT contain
   `task_payload`.  The keys actually present are: jsonrpc_request_id,
   logical_task_id, session_id, user_id, a2a_user_config.  Consequently
   `hoist_payload_state` is a safe no-op on real SAM state — it reads
   `a2a_context.task_payload` which is always absent.

3. On the mesh path, `domain_vector_store_id` reaches the tools via LLM
   args (args-first branch in SearchPrecedentsTool/_run_async_impl):
   the runner injects CaseState into the user-message DataPart so the
   LLM can read `domain_vector_store_id` and pass it as a tool call
   argument.  The state-fallback branch is the hook's intended path but
   is currently unreachable with the installed SAM version.

The hook remains in place to be ready if a future SAM version surfaces
task_payload, and because it is a functional no-op (safe to leave).
"""

from typing import Any

_PASSTHROUGH_STATE_KEYS = ("domain_vector_store_id",)


def hoist_payload_state(
    tool: Any,
    args: dict,
    tool_context: Any,
    host_component: Any = None,
) -> None:
    """Copy CaseState fields from A2A payload into tool_context.state.

    Called as an ADK before_tool_callback before each tool invocation.
    No-op when tool_context.state is not a dict or when the payload key
    is absent — safe to register unconditionally.
    """
    state = getattr(tool_context, "state", None)
    if not isinstance(state, dict):
        return
    # Try the key path confirmed by pre-impl verification
    a2a_ctx = state.get("a2a_context") or {}
    payload = a2a_ctx.get("task_payload") or {}
    for key in _PASSTHROUGH_STATE_KEYS:
        if key not in state and key in payload:
            state[key] = payload[key]
