"""MLflow tracing bootstrap for VerdictCouncil pipeline.

Never activated at module scope — call configure_mlflow() explicitly from
FastAPI startup or the eval runner fixture. This prevents autolog from
firing during pytest collection.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager, suppress
from typing import Any

from src.shared.config import settings

logger = logging.getLogger(__name__)

_CONFIGURED = False


def configure_mlflow() -> None:
    """Idempotent: activate MLflow OpenAI autolog + set experiment. No-op when disabled."""
    global _CONFIGURED
    if _CONFIGURED or not settings.mlflow_enabled:
        return
    try:
        import mlflow

        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        mlflow.set_experiment(settings.mlflow_experiment)
        # Disable trace autologging. The OpenAI autolog defaults to
        # `log_traces=True`, which writes trace spans to the experiment's
        # configured artifact_root. Our MLflow server is containerised
        # with artifact_root=/mlflow/artifacts (inside the container),
        # but the client (this worker / API process) tries to write to
        # that same literal path on the host, which doesn't exist and is
        # read-only on macOS/Linux. The logged-run metadata still lands
        # on the tracking server over HTTP, so we keep the core autolog
        # but skip the trace export path that was spamming
        # `[Errno 30] Read-only file system: '/mlflow'` warnings.
        mlflow.openai.autolog(log_traces=False)
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
    with mlflow.start_run(run_name=run_name, nested=nested) as run:
        mlflow.set_tags({"case_id": case_id, "run_id": run_id, "pipeline_mode": mode})
        yield run


@contextmanager
def agent_run(*, agent_name: str, case_id: str, run_id: str) -> Generator[tuple[str, str] | None, None, None]:
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
    with mlflow.start_run(run_name=run_name, nested=True) as run:
        mlflow.set_tags(
            {
                "case_id": case_id,
                "run_id": run_id,
                "agent_name": agent_name,
                "pipeline_stage": "agent",
            }
        )
        yield (run.info.run_id, run.info.experiment_id)


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
