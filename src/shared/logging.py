import json
import logging
import logging.handlers
import sys
from contextvars import ContextVar
from pathlib import Path

case_id_var: ContextVar[str | None] = ContextVar("case_id", default=None)
run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)


class CorrelationFilter(logging.Filter):
    """Adds case_id and run_id to log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.case_id = case_id_var.get() or "-"  # type: ignore[attr-defined]
        record.run_id = run_id_var.get() or "-"  # type: ignore[attr-defined]
        return True


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line for structured file tailing."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "case_id": getattr(record, "case_id", "-"),
            "run_id": getattr(record, "run_id", "-"),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging with correlation IDs.

    Two handlers:
    - stdout: human-readable with correlation IDs (always on)
    - logs/pipeline.jsonl: rotating JSON file for tailing during debugging
    """
    correlation = CorrelationFilter()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.addFilter(correlation)
    stdout_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] [case=%(case_id)s run=%(run_id)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    Path("logs").mkdir(exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        "logs/pipeline.jsonl",
        maxBytes=20_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.addFilter(correlation)
    file_handler.setFormatter(_JsonFormatter())

    logging.root.handlers = [stdout_handler, file_handler]
    logging.root.setLevel(getattr(logging, level.upper(), logging.INFO))


def set_correlation_ids(case_id: str | None = None, run_id: str | None = None) -> None:
    """Set correlation IDs for the current async context."""
    if case_id is not None:
        case_id_var.set(case_id)
    if run_id is not None:
        run_id_var.set(run_id)
