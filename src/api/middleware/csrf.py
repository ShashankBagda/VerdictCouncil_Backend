"""CSRF double-submit cookie middleware for VerdictCouncil.

Implements the double-submit cookie pattern:
  1. On GET /api/v1/auth/csrf-token — generate a random token, set it in a
     non-httpOnly cookie (``vc_csrf``), and return it in the response body.
  2. On every state-mutating request (POST, PUT, PATCH, DELETE) — compare
     the ``X-CSRF-Token`` request header against the ``vc_csrf`` cookie value.
     Reject with 403 if they do not match or either is missing.

Safe methods (GET, HEAD, OPTIONS) are passed through unconditionally.
The ``/api/v1/auth/csrf-token`` endpoint itself is exempt.

Security notes
--------------
* Requires ``SameSite=Lax`` (already set on ``vc_token`` auth cookie) as the
  primary CSRF defence.  This middleware is defence-in-depth per risk register
  item 11 (see docs/architecture/11-ai-security-risk-register.md).
* The CSRF token is generated with ``secrets.token_urlsafe(32)`` (256-bit
  entropy) and validated with ``hmac.compare_digest`` to prevent timing
  attacks.
* The ``vc_csrf`` cookie is NOT httpOnly so the JavaScript client can read it
  and echo it in the request header.  It shares the ``SameSite=Strict`` and
  ``Secure`` attributes when running in production (``settings.secure_cookies``).
"""

from __future__ import annotations

import hmac
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.shared.config import settings

# HTTP methods that do NOT mutate state — always let through
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Endpoints that are exempt from CSRF checking
_EXEMPT_PATHS = frozenset({
    "/api/v1/auth/csrf-token",
    "/api/v1/auth/login",   # login issues the CSRF token; exempting avoids chicken-and-egg
    "/api/v1/auth/logout",  # no state mutation risk; cookie is already present
    "/health",
    "/metrics",
    "/openapi.json",
    "/docs",
    "/redoc",
})

CSRF_COOKIE_NAME = "vc_csrf"
CSRF_HEADER_NAME = "x-csrf-token"
TOKEN_BYTES = 32  # 256-bit entropy


def generate_csrf_token() -> str:
    """Return a URL-safe base64 CSRF token (256-bit entropy)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def _tokens_match(a: str, b: str) -> bool:
    """Constant-time comparison to prevent timing side-channels."""
    return hmac.compare_digest(a.encode(), b.encode())


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF protection middleware."""

    def __init__(self, app, *, secure: bool | None = None):
        super().__init__(app)
        # Fall back to settings if not explicitly supplied (allows test override)
        self._secure = secure if secure is not None else getattr(settings, "secure_cookies", False)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Safe methods and exempt paths pass through without CSRF check
        if request.method in _SAFE_METHODS or request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
        header_token = request.headers.get(CSRF_HEADER_NAME, "")

        if not cookie_token or not header_token:
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing"},
            )

        if not _tokens_match(cookie_token, header_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token mismatch"},
            )

        return await call_next(request)

    def set_csrf_cookie(self, response: Response, token: str) -> None:
        """Attach the CSRF cookie to an outgoing response."""
        response.set_cookie(
            CSRF_COOKIE_NAME,
            value=token,
            httponly=False,   # Must be readable by JS to echo in the header
            samesite="strict",
            secure=self._secure,
            path="/",
            max_age=3600,     # 1-hour lifetime; renewed on activity
        )
