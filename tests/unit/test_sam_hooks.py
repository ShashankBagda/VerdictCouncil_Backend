"""Unit tests for src.tools.sam.hooks — hoist_payload_state."""

from types import SimpleNamespace

from src.tools.sam.hooks import hoist_payload_state


def _make_tool_context(state) -> SimpleNamespace:
    """Build a minimal tool_context stand-in with the given state."""
    return SimpleNamespace(state=state)


# ------------------------------------------------------------------ #
# Happy path: domain_vector_store_id is hoisted from task payload
# ------------------------------------------------------------------ #


def test_hoist_payload_state_copies_domain_vector_store_id():
    """domain_vector_store_id from the A2A task payload is hoisted into state."""
    state = {
        "a2a_context": {
            "task_payload": {
                "domain_vector_store_id": "vs_abc",
            }
        }
    }
    ctx = _make_tool_context(state)
    hoist_payload_state(tool=None, args={}, tool_context=ctx)

    assert state["domain_vector_store_id"] == "vs_abc"


def test_hoist_payload_state_does_not_remove_existing_keys():
    """Hoisting does not strip other keys already present in state."""
    state = {
        "some_other_key": "value",
        "a2a_context": {
            "task_payload": {
                "domain_vector_store_id": "vs_abc",
            }
        },
    }
    ctx = _make_tool_context(state)
    hoist_payload_state(tool=None, args={}, tool_context=ctx)

    assert state["some_other_key"] == "value"
    assert state["domain_vector_store_id"] == "vs_abc"


# ------------------------------------------------------------------ #
# No-op when state is not a dict
# ------------------------------------------------------------------ #


def test_hoist_payload_state_noop_when_state_is_none():
    """No-op when tool_context.state is None — must not raise."""
    ctx = _make_tool_context(None)
    hoist_payload_state(tool=None, args={}, tool_context=ctx)  # should not raise


def test_hoist_payload_state_noop_when_state_is_list():
    """No-op when tool_context.state is a list (not a dict)."""
    ctx = _make_tool_context([])
    hoist_payload_state(tool=None, args={}, tool_context=ctx)  # should not raise


def test_hoist_payload_state_noop_when_state_is_string():
    """No-op when tool_context.state is a string (not a dict)."""
    ctx = _make_tool_context("not-a-dict")
    hoist_payload_state(tool=None, args={}, tool_context=ctx)  # should not raise


def test_hoist_payload_state_noop_when_tool_context_has_no_state():
    """No-op when tool_context has no state attribute."""
    ctx = SimpleNamespace()  # no .state attribute
    hoist_payload_state(tool=None, args={}, tool_context=ctx)  # should not raise


# ------------------------------------------------------------------ #
# No-op when domain_vector_store_id already in state (no overwrite)
# ------------------------------------------------------------------ #


def test_hoist_payload_state_does_not_overwrite_existing_domain_vector_store_id():
    """If domain_vector_store_id is already in state, do not overwrite it."""
    state = {
        "domain_vector_store_id": "vs_already_set",
        "a2a_context": {
            "task_payload": {
                "domain_vector_store_id": "vs_from_payload",
            }
        },
    }
    ctx = _make_tool_context(state)
    hoist_payload_state(tool=None, args={}, tool_context=ctx)

    # Must preserve the original value, not the payload value
    assert state["domain_vector_store_id"] == "vs_already_set"


# ------------------------------------------------------------------ #
# No-op when payload key is absent
# ------------------------------------------------------------------ #


def test_hoist_payload_state_noop_when_a2a_context_absent():
    """No a2a_context in state — must not add domain_vector_store_id."""
    state = {"some_key": "value"}
    ctx = _make_tool_context(state)
    hoist_payload_state(tool=None, args={}, tool_context=ctx)

    assert "domain_vector_store_id" not in state


def test_hoist_payload_state_noop_when_task_payload_absent():
    """a2a_context present but no task_payload — must not add domain_vector_store_id."""
    state = {"a2a_context": {}}
    ctx = _make_tool_context(state)
    hoist_payload_state(tool=None, args={}, tool_context=ctx)

    assert "domain_vector_store_id" not in state


def test_hoist_payload_state_noop_when_domain_key_absent_from_payload():
    """task_payload present but no domain_vector_store_id key — must not add to state."""
    state = {
        "a2a_context": {
            "task_payload": {
                "other_field": "other_value",
            }
        }
    }
    ctx = _make_tool_context(state)
    hoist_payload_state(tool=None, args={}, tool_context=ctx)

    assert "domain_vector_store_id" not in state


# ------------------------------------------------------------------ #
# host_component argument is optional
# ------------------------------------------------------------------ #


def test_hoist_payload_state_accepts_host_component_kwarg():
    """hoist_payload_state accepts an optional host_component kwarg (ADK signature)."""
    state = {
        "a2a_context": {
            "task_payload": {
                "domain_vector_store_id": "vs_abc",
            }
        }
    }
    ctx = _make_tool_context(state)
    # Must not raise when host_component is supplied
    hoist_payload_state(tool=None, args={}, tool_context=ctx, host_component=object())

    assert state["domain_vector_store_id"] == "vs_abc"
