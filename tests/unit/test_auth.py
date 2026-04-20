"""Unit tests for auth endpoints (POST register, login, logout, GET /me)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_db
from src.models.user import User, UserRole
from src.shared.config import settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(**overrides) -> MagicMock:
    """Return a mock User with sensible defaults."""
    defaults = {
        "id": uuid.uuid4(),
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "role": UserRole.judge,
        "password_hash": "$2b$12$KIXbCWq9EkP5V0FfG5.JIexxxxxx",
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }
    defaults.update(overrides)
    user = MagicMock(spec=User)
    for k, v in defaults.items():
        setattr(user, k, v)
    return user


def _mock_scalar_result(value):
    """Wrap a value in a mock Result whose .scalar_one_or_none() returns it."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _build_mock_session():
    """Return an AsyncMock that mimics an AsyncSession."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


def _mint_token(user_id: uuid.UUID, role: str = "judge") -> str:
    from datetime import timedelta

    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": datetime.now(UTC) + timedelta(hours=24),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRegister:
    async def test_register_creates_user(self):
        """POST /register with valid data returns 201 and user without password."""
        mock_db = _build_mock_session()
        # First execute: check if email exists -> None
        mock_db.execute.return_value = _mock_scalar_result(None)

        new_user_id = uuid.uuid4()
        now = datetime.now(UTC)

        async def _refresh(user):
            user.id = new_user_id
            user.created_at = now
            user.updated_at = None

        mock_db.refresh.side_effect = _refresh

        app = create_app()
        app.dependency_overrides[get_db] = lambda: mock_db

        transport = ASGITransport(app=app)
        with patch("src.api.routes.auth.pwd_context") as mock_pwd:
            mock_pwd.hash.return_value = "hashed_password"
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/register",
                    json={
                        "name": "Ada Lovelace",
                        "email": "ada@example.com",
                        "password": "secureP@ss1",
                        "role": "judge",
                    },
                )

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Ada Lovelace"
        assert data["email"] == "ada@example.com"
        assert data["role"] == "judge"
        assert "password" not in data
        assert "password_hash" not in data

    async def test_register_duplicate_email(self):
        """POST /register with existing email returns 409."""
        existing_user = _make_user()
        mock_db = _build_mock_session()
        mock_db.execute.return_value = _mock_scalar_result(existing_user)

        app = create_app()
        app.dependency_overrides[get_db] = lambda: mock_db

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/auth/register",
                json={
                    "name": "Ada Lovelace",
                    "email": "ada@example.com",
                    "password": "secureP@ss1",
                    "role": "judge",
                },
            )

        assert resp.status_code == 409
        assert "already registered" in resp.json()["detail"].lower()


class TestLogin:
    async def test_login_valid_credentials(self):
        """POST /login with correct credentials returns 200 and sets vc_token cookie."""
        user = _make_user()

        mock_db = _build_mock_session()
        mock_db.execute.return_value = _mock_scalar_result(user)

        app = create_app()
        app.dependency_overrides[get_db] = lambda: mock_db

        transport = ASGITransport(app=app)
        with patch("src.api.routes.auth.pwd_context") as mock_pwd:
            mock_pwd.verify.return_value = True

            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/login",
                    json={"email": "ada@example.com", "password": "secureP@ss1"},
                )

        assert resp.status_code == 200
        assert resp.json()["message"] == "logged in"
        # Cookie should be set
        cookie_header = resp.headers.get("set-cookie", "")
        assert "vc_token" in cookie_header
        assert "httponly" in cookie_header.lower()

    async def test_login_invalid_password(self):
        """POST /login with wrong password returns 401."""
        user = _make_user()

        mock_db = _build_mock_session()
        mock_db.execute.return_value = _mock_scalar_result(user)

        app = create_app()
        app.dependency_overrides[get_db] = lambda: mock_db

        transport = ASGITransport(app=app)
        with patch("src.api.routes.auth.pwd_context") as mock_pwd:
            mock_pwd.verify.return_value = False

            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/login",
                    json={"email": "ada@example.com", "password": "wrongpass"},
                )

        assert resp.status_code == 401
        assert "invalid" in resp.json()["detail"].lower()

    async def test_login_nonexistent_user(self):
        """POST /login with unknown email returns 401."""
        mock_db = _build_mock_session()
        mock_db.execute.return_value = _mock_scalar_result(None)

        app = create_app()
        app.dependency_overrides[get_db] = lambda: mock_db

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/auth/login",
                json={"email": "ghost@example.com", "password": "anything"},
            )

        assert resp.status_code == 401


class TestLogout:
    async def test_logout_clears_cookie(self):
        """POST /logout returns 200 and clears the vc_token cookie."""
        app = create_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/auth/logout")

        assert resp.status_code == 200
        assert resp.json()["message"] == "logged out"
        cookie_header = resp.headers.get("set-cookie", "")
        assert "vc_token" in cookie_header
        # Cleared cookies have max-age=0 or expires in the past
        assert "max-age=0" in cookie_header.lower() or "1970" in cookie_header


class TestMe:
    async def test_me_returns_current_user(self):
        """GET /me with a valid JWT cookie returns 200 and user data."""
        user_id = uuid.uuid4()
        user = _make_user(id=user_id)
        token = _mint_token(user_id)

        mock_db = _build_mock_session()
        # get_current_user will call db.execute to look up the user by id
        mock_db.execute.return_value = _mock_scalar_result(user)

        app = create_app()
        app.dependency_overrides[get_db] = lambda: mock_db

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/auth/me",
                cookies={"vc_token": token},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "ada@example.com"
        assert data["name"] == "Ada Lovelace"
        assert "password" not in data
        assert "password_hash" not in data

    async def test_me_no_cookie(self):
        """GET /me without a cookie returns 401."""
        app = create_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/auth/me")

        assert resp.status_code == 401
