import logging
import sys
from contextvars import ContextVar

case_id_var: ContextVar[str | None] = ContextVar("case_id", default=None)
run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)


class CorrelationFilter(logging.Filter):
    """Adds case_id and run_id to log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.case_id = case_id_var.get() or "-"  # type: ignore[attr-defined]
        record.run_id = run_id_var.get() or "-"  # type: ignore[attr-defined]
        return True


def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging with correlation IDs."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(CorrelationFilter())
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [case=%(case_id)s run=%(run_id)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logging.root.handlers = [handler]
    logging.root.setLevel(getattr(logging, level.upper(), logging.INFO))


def set_correlation_ids(case_id: str | None = None, run_id: str | None = None) -> None:
    """Set correlation IDs for the current async context."""
    if case_id is not None:
        case_id_var.set(case_id)
    if run_id is not None:
        run_id_var.set(run_id)
