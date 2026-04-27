"""Sprint 1 1.DEP1.1 — `langgraph.json` smoke validation.

Locks down the LangGraph CLI config:

- File parses as JSON.
- The `graphs` entry resolves to a real, importable callable.
- The `env` and `dependencies` paths exist on disk.
- The Python version matches the rest of the project (`pyproject.toml`
  pins `requires-python = ">=3.12"`; `tool.ruff.target-version = py312`;
  `tool.mypy.python_version = "3.12"`).

These checks would fail loudly if anyone moved `build_graph`, deleted
`.env`, or pinned the CLI to a Python version that disagrees with the
rest of the toolchain.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LANGGRAPH_CONFIG = REPO_ROOT / "langgraph.json"


@pytest.fixture(scope="module")
def config() -> dict:
    assert LANGGRAPH_CONFIG.exists(), (
        f"langgraph.json missing at {LANGGRAPH_CONFIG}; required by 1.DEP1.1"
    )
    return json.loads(LANGGRAPH_CONFIG.read_text())


def test_config_declares_a_single_graph_named_verdictcouncil(config: dict) -> None:
    assert "graphs" in config and isinstance(config["graphs"], dict)
    assert list(config["graphs"]) == ["verdictcouncil"], (
        f"Sprint 1 ships exactly one graph named 'verdictcouncil'; got {list(config['graphs'])}"
    )


def test_graph_entrypoint_resolves_to_callable_factory(config: dict) -> None:
    """Acceptance criterion: graph entry resolvable to a callable factory.

    Sprint 1 1.DEP1.2: `langgraph.json` points at `make_graph` (one-arg
    CLI factory), not `build_graph` (two-arg runner factory). The CLI
    rejects multi-arg factories with "must take exactly one argument,
    a RunnableConfig".
    """
    import inspect

    entry = config["graphs"]["verdictcouncil"]
    rel_path, _, symbol = entry.partition(":")
    assert rel_path.endswith(".py") and symbol, (
        f"Graph entry must be 'path/to/file.py:symbol'; got {entry!r}"
    )

    target = (REPO_ROOT / rel_path).resolve()
    assert target.exists(), f"langgraph.json points at {target} which does not exist"

    # Convert path → dotted module name relative to the backend repo root.
    rel = target.relative_to(REPO_ROOT)
    module_name = ".".join(rel.with_suffix("").parts)
    module = importlib.import_module(module_name)

    fn = getattr(module, symbol, None)
    assert callable(fn), (
        f"langgraph.json's graph entry {entry!r} resolves to {fn!r}, not a callable."
    )

    # CLI strict-check parity: factory must take exactly one positional arg.
    sig = inspect.signature(fn)
    assert len(sig.parameters) == 1, (
        f"langgraph.json factory {symbol!r} must take exactly one positional arg "
        f"(a RunnableConfig); got {len(sig.parameters)} params. The LangGraph CLI "
        "fails with 'must take exactly one argument' otherwise."
    )


def test_env_path_exists_or_is_optional(config: dict) -> None:
    """`env` may legitimately be absent in CI, but the path key must be set."""
    assert "env" in config
    env_path = REPO_ROOT / config["env"]
    # `.env` is gitignored, so it may not be present in a fresh checkout —
    # the sample `.env.example` should be there as a reference.
    assert env_path.exists() or (REPO_ROOT / ".env.example").exists(), (
        f"langgraph.json points at {env_path} but neither it nor .env.example exists"
    )


def test_dependencies_paths_resolve(config: dict) -> None:
    deps = config.get("dependencies", [])
    assert deps, "langgraph.json must declare at least one dependency path"
    for dep in deps:
        # Dependencies are project-relative paths to Python packages.
        target = (REPO_ROOT / dep).resolve()
        assert target.exists(), f"langgraph.json dependency {dep!r} → {target} missing"


def test_python_version_matches_pyproject(config: dict) -> None:
    """Drift between LangGraph CLI's Python and pyproject's would break the CLI build."""
    declared = config.get("python_version")
    assert declared == "3.12", (
        f"langgraph.json declares Python {declared!r}; "
        "pyproject.toml requires >=3.12 and ruff/mypy target py312, so this must match."
    )
