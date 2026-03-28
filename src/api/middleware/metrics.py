"""Prometheus metrics for VerdictCouncil API.

Exposes /metrics endpoint with:
- http_requests_total (counter by method, path, status)
- http_request_duration_seconds (histogram by method, path)
- active_cases_total (gauge by status)

Lightweight implementation with no external dependencies.
"""

import threading
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

# Histogram bucket boundaries (in seconds)
_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class MetricsStore:
    """Thread-safe in-memory store for Prometheus-style metrics."""

    def __init__(self):
        self._lock = threading.Lock()
        # counter: {(method, path, status): count}
        self._request_counts: dict[tuple[str, str, str], int] = defaultdict(int)
        # histogram: {(method, path): {"sum": float, "count": int, "buckets": {bound: int}}}
        self._durations: dict[tuple[str, str], dict] = {}
        # gauge: {status: count}
        self._case_gauges: dict[str, int] = defaultdict(int)

    def inc_request(self, method: str, path: str, status: int) -> None:
        with self._lock:
            self._request_counts[(method, path, str(status))] += 1

    def observe_duration(self, method: str, path: str, duration: float) -> None:
        key = (method, path)
        with self._lock:
            if key not in self._durations:
                self._durations[key] = {
                    "sum": 0.0,
                    "count": 0,
                    "buckets": {b: 0 for b in _BUCKETS},
                }
            entry = self._durations[key]
            entry["sum"] += duration
            entry["count"] += 1
            for bound in _BUCKETS:
                if duration <= bound:
                    entry["buckets"][bound] += 1

    def set_case_gauge(self, status: str, count: int) -> None:
        with self._lock:
            self._case_gauges[status] = count

    def render(self) -> str:
        """Render all metrics in Prometheus text exposition format."""
        lines: list[str] = []

        with self._lock:
            # http_requests_total
            if self._request_counts:
                lines.append("# HELP http_requests_total Total HTTP requests.")
                lines.append("# TYPE http_requests_total counter")
                for (method, path, st), count in sorted(self._request_counts.items()):
                    lines.append(
                        f"http_requests_total"
                        f'{{method="{method}",path="{path}"'
                        f',status="{st}"}} {count}'
                    )

            # http_request_duration_seconds
            if self._durations:
                lines.append(
                    "# HELP http_request_duration_seconds HTTP request duration in seconds."
                )
                lines.append("# TYPE http_request_duration_seconds histogram")
                for (method, path), entry in sorted(self._durations.items()):
                    labels = f'method="{method}",path="{path}"'
                    for bound, cnt in sorted(entry["buckets"].items()):
                        lines.append(
                            f'http_request_duration_seconds_bucket{{{labels},le="{bound}"}} {cnt}'
                        )
                    lines.append(
                        f"http_request_duration_seconds_bucket"
                        f'{{{labels},le="+Inf"}} {entry["count"]}'
                    )
                    lines.append(
                        f"http_request_duration_seconds_sum{{{labels}}} {entry['sum']:.6f}"
                    )
                    lines.append(
                        f"http_request_duration_seconds_count{{{labels}}} {entry['count']}"
                    )

            # active_cases_total
            if self._case_gauges:
                lines.append("# HELP active_cases_total Active cases by status.")
                lines.append("# TYPE active_cases_total gauge")
                for status, count in sorted(self._case_gauges.items()):
                    lines.append(f'active_cases_total{{status="{status}"}} {count}')

        lines.append("")  # trailing newline
        return "\n".join(lines)


# Module-level singleton so the /metrics endpoint can access the same store.
metrics_store = MetricsStore()


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware that records request counts and durations."""

    async def dispatch(self, request: Request, call_next):
        # Skip recording the /metrics endpoint itself
        if request.url.path == "/metrics":
            return await call_next(request)

        method = request.method
        path = request.url.path
        start = time.monotonic()

        response = await call_next(request)

        duration = time.monotonic() - start
        metrics_store.inc_request(method, path, response.status_code)
        metrics_store.observe_duration(method, path, duration)

        return response


def metrics_endpoint(_request: Request) -> PlainTextResponse:
    """Handler for GET /metrics."""
    return PlainTextResponse(
        content=metrics_store.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
