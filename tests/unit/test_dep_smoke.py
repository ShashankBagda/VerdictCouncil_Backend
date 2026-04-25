"""Sprint 1 dependency-migration smoke tests (Task 1.A1.0).

Verifies the LangChain 1.x ecosystem upgrade:
- imports resolve for `create_agent`, middleware decorators, LangGraph
  primitives (`Send`, `interrupt`, `Command`), the postgres checkpointer,
  and the LangSmith client
- `create_agent` constructs against a fake chat model
- `response_format=PydanticSchema` returns a validated instance
- no DeprecationWarnings are raised by the smoke surface

These imports and APIs are P0 prerequisites for every other Sprint 1
task; if anything here regresses, halt Sprint 1.
"""

from __future__ import annotations

import warnings

import pytest


def test_core_imports_resolve() -> None:
    """All Sprint 1 entry-point symbols must import cleanly."""
    from langchain.agents import create_agent  # noqa: F401
    from langchain.agents.middleware import (  # noqa: F401
        wrap_model_call,
        wrap_tool_call,
    )
    from langgraph.checkpoint.postgres import PostgresSaver  # noqa: F401
    from langgraph.types import Command, Send, interrupt  # noqa: F401
    from langsmith import Client  # noqa: F401


def test_create_agent_constructs_against_fake_model() -> None:
    """`create_agent` should build a runnable agent given a fake chat model."""
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import (
        FakeMessagesListChatModel,
    )
    from langchain_core.messages import AIMessage

    fake = FakeMessagesListChatModel(responses=[AIMessage(content="echo")])
    agent = create_agent(model=fake, tools=[], system_prompt="echo")

    assert agent is not None


class _ToolCallingFakeModel:
    """Minimal hand-rolled fake supporting `bind_tools` + a fixed tool call.

    `langchain_core.language_models.fake_chat_models.{FakeMessagesListChatModel,
    GenericFakeChatModel}` do not override `bind_tools`, so they cannot be used
    with `create_agent(response_format=Schema)` (which routes through
    `ToolStrategy` and binds the schema as a tool). This local subclass plugs
    that gap for the dep smoke test only — production code uses `ChatOpenAI`.
    """

    @staticmethod
    def build(tool_name: str, tool_args: dict) -> object:
        from langchain_core.language_models import BaseChatModel
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        class _Fake(BaseChatModel):
            tool_name: str
            tool_args: dict

            @property
            def _llm_type(self) -> str:
                return "tool-calling-fake"

            def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[override]
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[override]
                msg = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": self.tool_name,
                            "args": self.tool_args,
                            "id": "test-1",
                            "type": "tool_call",
                        }
                    ],
                )
                return ChatResult(generations=[ChatGeneration(message=msg)])

        return _Fake(tool_name=tool_name, tool_args=tool_args)


def test_response_format_returns_validated_pydantic_instance() -> None:
    """`create_agent(response_format=Schema)` must return a Schema instance."""
    from langchain.agents import create_agent
    from pydantic import BaseModel, ConfigDict, Field

    class EchoOutput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        message: str = Field(min_length=1)

    fake = _ToolCallingFakeModel.build(
        tool_name="EchoOutput", tool_args={"message": "hello"}
    )
    agent = create_agent(
        model=fake,
        tools=[],
        system_prompt="produce echo output",
        response_format=EchoOutput,
    )

    result = agent.invoke({"messages": [("user", "say hi")]})
    structured = result.get("structured_response")
    assert isinstance(structured, EchoOutput), (
        f"expected EchoOutput, got {type(structured).__name__}: {result!r}"
    )
    assert structured.message == "hello"


def test_smoke_surface_emits_no_deprecation_warnings() -> None:
    """Importing and using the Sprint 1 surface should not raise DeprecationWarning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        from langchain.agents import create_agent  # noqa: F401
        from langchain.agents.middleware import (  # noqa: F401
            wrap_model_call,
            wrap_tool_call,
        )
        from langgraph.checkpoint.postgres import PostgresSaver  # noqa: F401
        from langgraph.types import Command, Send, interrupt  # noqa: F401
        from langsmith import Client  # noqa: F401

    deprecations = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert not deprecations, (
        "DeprecationWarning raised by Sprint 1 dep surface:\n"
        + "\n".join(f"  - {w.filename}:{w.lineno} {w.message}" for w in deprecations)
    )
