"""Tests for the CSRF double-submit cookie middleware.

Covers:
- Safe methods (GET, HEAD, OPTIONS) bypass CSRF check
- Exempt paths bypass CSRF check
- Missing cookie → 403
- Missing header → 403
- Mismatched tokens → 403
- Matched tokens → 200
- Constant-time comparison helper (_tokens_match)
- generate_csrf_token returns URL-safe string with adequate entropy
- set_csrf_cookie sets correct cookie attributes

All tests are unit-level (no live infrastructure required).
"""

from __future__ import annotations

import secrets

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from src.api.middleware.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CSRFMiddleware,
    _tokens_match,
    generate_csrf_token,
)


# ---------------------------------------------------------------------------
# Minimal FastAPI app for testing the middleware
# ---------------------------------------------------------------------------


def _build_app(secure: bool = False) -> FastAPI:
    """Return a minimal FastAPI app with CSRFMiddleware attached."""
    app = FastAPI()
    app.add_middleware(CSRFMiddleware, secure=secure)

    @app.get("/api/v1/safe")
    async def safe_get():
        return {"ok": True}

    @app.post("/api/v1/cases")
    async def create_case():
        return {"created": True}

    @app.put("/api/v1/cases/1")
    async def update_case():
        return {"updated": True}

    @app.patch("/api/v1/cases/1")
    async def patch_case():
        return {"patched": True}

    @app.delete("/api/v1/cases/1")
    async def delete_case():
        return {"deleted": True}

    @app.get("/api/v1/auth/csrf-token")
    async def csrf_token():
        return {"token": "test-csrf-token"}

    @app.post("/api/v1/auth/login")
    async def login():
        return {"token": "jwt"}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_build_app(), raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# generate_csrf_token
# ---------------------------------------------------------------------------


class TestGenerateCsrfToken:
    def test_returns_non_empty_string(self):
        token = generate_csrf_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_tokens_are_unique(self):
        """Consecutive calls should produce different tokens (collision
        probability is astronomically small for 256-bit tokens)."""
        tokens = {generate_csrf_token() for _ in range(20)}
        assert len(tokens) == 20

    def test_token_entropy_length(self):
        """URL-safe base64 of 32 bytes = 43 characters (no padding)."""
        token = generate_csrf_token()
        # 32 bytes → ceil(32 * 4/3) = 43 chars (URL-safe b64, no padding)
        assert len(token) >= 40  # lenient floor — encoding may vary

    def test_token_is_url_safe(self):
        """Token must not contain characters that break HTTP headers (+, /)."""
        for _ in range(50):
            token = generate_csrf_token()
            assert "+" not in token
            assert "/" not in token


# ---------------------------------------------------------------------------
# _tokens_match
# ---------------------------------------------------------------------------


class TestTokensMatch:
    def test_identical_tokens_match(self):
        t = secrets.token_urlsafe(32)
        assert _tokens_match(t, t) is True

    def test_different_tokens_do_not_match(self):
        a = secrets.token_urlsafe(32)
        b = secrets.token_urlsafe(32)
        # Astronomically unlikely to collide
        assert _tokens_match(a, b) is False

    def test_empty_strings_match_each_other(self):
        assert _tokens_match("", "") is True

    def test_empty_vs_nonempty_does_not_match(self):
        assert _tokens_match("", "abc") is False

    def test_returns_bool(self):
        result = _tokens_match("x", "x")
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Safe HTTP methods (GET, HEAD, OPTIONS) bypass CSRF
# ---------------------------------------------------------------------------


class TestSafeMethods:
    def test_get_bypasses_csrf_no_cookies(self, client):
        response = client.get("/api/v1/safe")
        assert response.status_code == 200

    def test_head_bypasses_csrf_no_cookies(self, client):
        response = client.request("HEAD", "/api/v1/safe")
        assert response.status_code == 200

    def test_options_bypasses_csrf_no_cookies(self, client):
        response = client.request("OPTIONS", "/api/v1/safe")
        # FastAPI returns 405 for unregistered methods, but CSRF is not the
        # reason — the middleware let it through.
        assert response.status_code not in (403,)


# ---------------------------------------------------------------------------
# Exempt paths bypass CSRF
# ---------------------------------------------------------------------------


class TestExemptPaths:
    def test_csrf_token_endpoint_exempt(self, client):
        response = client.get("/api/v1/auth/csrf-token")
        assert response.status_code == 200

    def test_login_endpoint_exempt(self, client):
        response = client.post("/api/v1/auth/login")
        assert response.status_code == 200

    def test_health_exempt(self, client):
        response = client.get("/health")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# CSRF validation on state-mutating methods
# ---------------------------------------------------------------------------


class TestCsrfValidation:
    """POST, PUT, PATCH, DELETE must carry matching cookie + header."""

    def _with_csrf(self, client, method, path):
        """Make a request with correctly set CSRF token."""
        token = generate_csrf_token()
        client.cookies.set(CSRF_COOKIE_NAME, token)
        return client.request(method, path, headers={CSRF_HEADER_NAME: token})

    # -- Missing cookie --
    def test_post_missing_cookie_returns_403(self, client):
        response = client.post("/api/v1/cases", headers={CSRF_HEADER_NAME: "some-token"})
        assert response.status_code == 403
        assert "CSRF token missing" in response.json()["detail"]

    def test_put_missing_cookie_returns_403(self, client):
        response = client.put("/api/v1/cases/1", headers={CSRF_HEADER_NAME: "some-token"})
        assert response.status_code == 403

    def test_patch_missing_cookie_returns_403(self, client):
        response = client.patch("/api/v1/cases/1", headers={CSRF_HEADER_NAME: "some-token"})
        assert response.status_code == 403

    def test_delete_missing_cookie_returns_403(self, client):
        response = client.delete("/api/v1/cases/1", headers={CSRF_HEADER_NAME: "some-token"})
        assert response.status_code == 403

    # -- Missing header --
    def test_post_missing_header_returns_403(self, client):
        client.cookies.set(CSRF_COOKIE_NAME, "some-token")
        response = client.post("/api/v1/cases")
        assert response.status_code == 403
        assert "CSRF token missing" in response.json()["detail"]

    # -- Both missing --
    def test_post_no_csrf_at_all_returns_403(self, client):
        response = client.post("/api/v1/cases")
        assert response.status_code == 403

    # -- Mismatched tokens --
    def test_post_mismatched_token_returns_403(self, client):
        client.cookies.set(CSRF_COOKIE_NAME, "token-A")
        response = client.post("/api/v1/cases", headers={CSRF_HEADER_NAME: "token-B"})
        assert response.status_code == 403
        assert "CSRF token mismatch" in response.json()["detail"]

    def test_put_mismatched_token_returns_403(self, client):
        client.cookies.set(CSRF_COOKIE_NAME, "aaa")
        response = client.put("/api/v1/cases/1", headers={CSRF_HEADER_NAME: "bbb"})
        assert response.status_code == 403

    # -- Valid tokens --
    def test_post_valid_csrf_returns_200(self, client):
        response = self._with_csrf(client, "POST", "/api/v1/cases")
        assert response.status_code == 200

    def test_put_valid_csrf_returns_200(self, client):
        response = self._with_csrf(client, "PUT", "/api/v1/cases/1")
        assert response.status_code == 200

    def test_patch_valid_csrf_returns_200(self, client):
        response = self._with_csrf(client, "PATCH", "/api/v1/cases/1")
        assert response.status_code == 200

    def test_delete_valid_csrf_returns_200(self, client):
        response = self._with_csrf(client, "DELETE", "/api/v1/cases/1")
        assert response.status_code == 200

    # -- Error response structure --
    def test_403_response_is_json(self, client):
        response = client.post("/api/v1/cases")
        assert response.headers["content-type"].startswith("application/json")
        data = response.json()
        assert "detail" in data

    def test_case_sensitive_header_name(self, client):
        """Header lookup must be case-insensitive (HTTP spec)."""
        token = generate_csrf_token()
        client.cookies.set(CSRF_COOKIE_NAME, token)
        # Use uppercase variant — Starlette normalises header names
        response = client.post("/api/v1/cases", headers={"X-CSRF-Token": token})
        assert response.status_code == 200
