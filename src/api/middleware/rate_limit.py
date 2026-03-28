"""Rate limiting middleware for the VerdictCouncil API.

Uses a sliding window counter per client IP. Configurable via
settings. Returns 429 Too Many Requests when limit is exceeded.
"""

import threading
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory sliding window rate limiter per client IP."""

    def __init__(self, app, *, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.window_seconds = 60
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 300  # purge stale entries every 5 minutes

    def _client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _cleanup_expired(self, now: float) -> None:
        """Remove entries older than the window for all IPs."""
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        cutoff = now - self.window_seconds
        stale_keys = []
        for ip, timestamps in self._requests.items():
            self._requests[ip] = [t for t in timestamps if t > cutoff]
            if not self._requests[ip]:
                stale_keys.append(ip)
        for key in stale_keys:
            del self._requests[key]

    async def dispatch(self, request: Request, call_next):
        now = time.monotonic()
        client_ip = self._client_ip(request)
        cutoff = now - self.window_seconds

        with self._lock:
            self._cleanup_expired(now)

            # Trim timestamps outside the current window
            self._requests[client_ip] = [t for t in self._requests[client_ip] if t > cutoff]

            if len(self._requests[client_ip]) >= self.requests_per_minute:
                # Calculate when the oldest request in the window expires
                oldest = self._requests[client_ip][0]
                retry_after = int(oldest + self.window_seconds - now) + 1
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests"},
                    headers={"Retry-After": str(retry_after)},
                )

            self._requests[client_ip].append(now)

        response = await call_next(request)
        return response
