"""MLflow tracing bootstrap for VerdictCouncil pipeline.

Never activated at module scope — call configure_mlflow() explicitly from
FastAPI startup or the eval runner fixture. This prevents autolog from
firing during pytest collection.
"""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Generator
from contextlib import contextmanager, suppress
from typing import Any

from src.shared.config import settings

logger = logging.getLogger(__name__)

_CONFIGURED = False


def configure_mlflow() -> None:
    """Idempotent: activate MLflow autolog for LangChain + OpenAI, set experiment."""
    global _CONFIGURED
    if _CONFIGURED or not settings.mlflow_enabled:
        return
    try:
        import mlflow

        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        mlflow.set_experiment(settings.mlflow_experiment)
        # LangChain/LangGraph autolog — traces every node, LLM call, and tool call.
        # run_tracer_inline=True is required for correct span nesting inside async handlers.
        mlflow.langchain.autolog(log_traces=True, run_tracer_inline=True)
        # OpenAI autolog — log_traces=True is safe now that the server uses
        # --serve-artifacts so clients write via mlflow-artifacts:// (HTTP proxy)
        # rather than the literal /mlflow/artifacts host path.
        mlflow.openai.autolog(log_traces=True)
        _CONFIGURED = True
        logger.info(
            "MLflow tracing enabled: uri=%s experiment=%s",
            settings.mlflow_tracking_uri,
            settings.mlflow_experiment,
        )
    except Exception as exc:
        logger.warning("MLflow configuration failed; continuing without tracing: %s", exc)


@contextmanager
def pipeline_run(*, case_id: str, run_id: str, mode: str) -> Generator[Any, None, None]:
    """Wrap one pipeline execution as an MLflow run.

    No-op when MLflow is disabled. Nested-safe: uses nested=True when an
    active run already exists (e.g. eval suite calling multiple cases).
    """
    if not settings.mlflow_enabled or not _CONFIGURED:
        yield None
        return
    import mlflow

    run_name = f"case_{case_id[:8]}_{run_id[:8]}"
    nested = mlflow.active_run() is not None
    start = time.perf_counter()
    with mlflow.start_run(run_name=run_name, nested=nested) as run:
        mlflow.log_params({"case_id": case_id, "run_id": run_id, "mode": mode})
        try:
            yield run
            mlflow.set_tag("status", "succeeded")
        except Exception:
            mlflow.set_tag("status", "failed")
            with suppress(Exception):
                mlflow.log_text(traceback.format_exc(), "error.txt")
            raise
        finally:
            with suppress(Exception):
                mlflow.log_metric("duration_s", time.perf_counter() - start)


@contextmanager
def agent_run(
    *, agent_name: str, case_id: str, run_id: str
) -> Generator[tuple[str, str] | None, None, None]:
    """Wrap one agent invocation as a nested MLflow run under the active pipeline run.

    Yields ``(mlflow_run_id, experiment_id)`` when MLflow is enabled so
    the mesh runner can surface them in the SSE `completed` event and
    the frontend can link straight to the MLflow UI for that agent.
    Yields ``None`` when MLflow is disabled or misconfigured.
    """
    if not settings.mlflow_enabled or not _CONFIGURED:
        yield None
        return
    import mlflow

    run_name = f"{agent_name}_{run_id[:8]}"
    start = time.perf_counter()
    with mlflow.start_run(run_name=run_name, nested=True) as run:
        mlflow.set_tags(
            {
                "case_id": case_id,
                "run_id": run_id,
                "agent_name": agent_name,
                "pipeline_stage": "agent",
            }
        )
        try:
            yield (run.info.run_id, run.info.experiment_id)
            mlflow.set_tag("status", "succeeded")
        except Exception:
            mlflow.set_tag("status", "failed")
            with suppress(Exception):
                mlflow.log_text(traceback.format_exc(), "error.txt")
            raise
        finally:
            with suppress(Exception):
                mlflow.log_metric("duration_s", time.perf_counter() - start)


@contextmanager
def tool_span(name: str, *, inputs: dict[str, Any] | None = None) -> Generator[Any, None, None]:
    """Manual span for custom tool calls not captured by OpenAI autolog.

    autolog traces AsyncOpenAI.chat.completions.create() calls only.
    Custom tools (parse_document, search_precedents, guardrails) need this.
    """
    if not settings.mlflow_enabled or not _CONFIGURED:
        yield None
        return
    import mlflow

    with mlflow.start_span(name=name) as span:
        if inputs is not None:
            with suppress(Exception):
                span.set_inputs(inputs)
        yield span
