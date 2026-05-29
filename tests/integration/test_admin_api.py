"""Integration tests for admin API endpoints with real DB."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.config import DashboardConfig
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.project import Project
from craft_dashboard.routes import admin as admin_routes
from craft_dashboard.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

_TEST_ADMIN_TOKEN = "test-admin-token"


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


@pytest.fixture
def app_with_db(test_db_session: AsyncSession) -> tuple[FastAPI, str]:
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.state.config = DashboardConfig()
    app.state.settings = Settings()
    app.state.settings.admin_token = _TEST_ADMIN_TOKEN
    token = _TEST_ADMIN_TOKEN

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield test_db_session

    app.dependency_overrides[get_db_session] = _override
    return app, token


@pytest.fixture
def client(app_with_db: tuple[FastAPI, str]) -> TestClient:
    app, _ = app_with_db
    with TestClient(app) as test_client:
        yield test_client


class TestAdminPageIntegration:
    """Integration tests for the admin dashboard page."""

    def test_admin_page_renders(
        self,
        client: TestClient,
        test_db_session: AsyncSession,
    ) -> None:
        """The admin page renders HTML when real DB data exists."""

        async def _seed() -> None:
            test_db_session.add(
                Project(
                    name="admin-project", category="application", github_org="canonical"
                )
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = client.get("/admin")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "<h2>Admin</h2>" in response.text

    def test_admin_page_empty_db(self, client: TestClient) -> None:
        """The admin page still renders when the database is empty."""
        response = client.get("/admin")

        assert response.status_code == 200
        assert "Project Refresh Schedule" in response.text


class TestAdminHealthIntegration:
    """Integration tests for the admin health endpoint."""

    def test_health_ok(
        self, client: TestClient, app_with_db: tuple[FastAPI, str]
    ) -> None:
        """Health returns ok with an empty but reachable DB."""
        _, token = app_with_db

        response = client.get(
            "/admin/health", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "database": "ok",
            "failing_collectors": [],
        }

    def test_health_requires_auth(self, client: TestClient) -> None:
        """Health requires a valid bearer token."""
        response = client.get("/admin/health")

        assert response.status_code == 401


class TestAdminAuthIntegration:
    """Integration tests for admin cookie authentication."""

    def test_admin_auth_sets_cookie_and_allows_cookie_auth(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
    ) -> None:
        """Valid admin auth sets a secure session cookie usable by admin routes."""
        _, token = app_with_db

        response = client.post(
            "/admin/auth",
            json={"token": token},
            headers={"Origin": "http://localhost", "Host": "localhost"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "authenticated"
        assert client.cookies.get("admin_session") == token
        set_cookie = response.headers["set-cookie"]
        assert "HttpOnly" in set_cookie
        assert "SameSite=strict" in set_cookie

        refresh_response = client.post(
            "/admin/refresh",
            headers={"Origin": "http://localhost", "Host": "localhost"},
        )

        assert refresh_response.status_code == 202
        assert refresh_response.json()["status"] == "refresh_queued"

    def test_admin_auth_rejects_invalid_token(self, client: TestClient) -> None:
        """Invalid admin auth requests are rejected."""
        response = client.post(
            "/admin/auth",
            json={"token": "wrong-token"},
            headers={"Origin": "http://localhost", "Host": "localhost"},
        )

        assert response.status_code == 401
        assert client.cookies.get("admin_session") is None

    def test_admin_logout_clears_cookie(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
    ) -> None:
        """Logout clears the admin session cookie."""
        _, token = app_with_db

        auth_response = client.post(
            "/admin/auth",
            json={"token": token},
            headers={"Origin": "http://localhost", "Host": "localhost"},
        )
        assert auth_response.status_code == 200

        response = client.post(
            "/admin/logout",
            headers={"Origin": "http://localhost", "Host": "localhost"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "logged_out"
        assert client.cookies.get("admin_session") is None
        assert "admin_session=" in response.headers["set-cookie"]

        refresh_response = client.post(
            "/admin/refresh",
            headers={"Origin": "http://localhost", "Host": "localhost"},
        )
        assert refresh_response.status_code == 401


class TestAdminRefreshIntegration:
    """Integration tests for refresh and re-evaluate endpoints."""

    @pytest.mark.parametrize(
        ("origin", "host", "expected_status"),
        [
            ("http://localhost", "localhost", 202),
            ("http://evil.com", "localhost", 403),
            ("http://evil.com?localhost", "localhost", 403),
        ],
    )
    def test_refresh_origin_validation(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
        origin: str,
        host: str,
        expected_status: int,
    ) -> None:
        """Refresh compares the parsed Origin netloc with Host exactly."""
        _, token = app_with_db

        response = client.post(
            "/admin/refresh",
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": origin,
                "Host": host,
            },
        )

        assert response.status_code == expected_status

    def test_re_evaluate_with_valid_token(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
    ) -> None:
        """Re-evaluate accepts a valid bearer token and queues work."""
        _, token = app_with_db

        response = client.post(
            "/admin/re-evaluate", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 202
        assert response.json()["status"] == "evaluation_queued"


class TestAdminLogsIntegration:
    """Integration tests for the admin logs endpoint."""

    def test_logs_with_valid_token(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
    ) -> None:
        """Logs returns plain text when called with a valid bearer token."""
        _, token = app_with_db

        response = client.get(
            "/admin/logs", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]

    def test_logs_returns_content(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
    ) -> None:
        """Logs always returns some body text, even without journalctl output."""
        _, token = app_with_db

        response = client.get(
            "/admin/logs", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert response.text.strip() != ""

    def test_logs_escape_html_entities(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Logs escape HTML entities before returning output."""
        _, token = app_with_db

        class _FakeProcess:
            async def communicate(self) -> tuple[bytes, bytes]:
                return (b"<script>alert(1)</script>", b"")

        async def _fake_create_subprocess_exec(*args, **kwargs):
            return _FakeProcess()

        monkeypatch.setattr(
            admin_routes.asyncio,
            "create_subprocess_exec",
            _fake_create_subprocess_exec,
        )

        response = client.get(
            "/admin/logs", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert response.text == "&lt;script&gt;alert(1)&lt;/script&gt;"

    def test_logs_use_async_subprocess_exec(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Logs use asyncio.create_subprocess_exec for journalctl."""
        _, token = app_with_db
        seen: dict[str, tuple | dict] = {}

        class _FakeProcess:
            async def communicate(self) -> tuple[bytes, bytes]:
                return (b"async logs", b"")

        async def _fake_create_subprocess_exec(*args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs
            return _FakeProcess()

        monkeypatch.setattr(
            admin_routes.asyncio,
            "create_subprocess_exec",
            _fake_create_subprocess_exec,
        )

        response = client.get(
            "/admin/logs", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert response.text == "async logs"
        assert seen["args"] == (
            "journalctl",
            "-u",
            "collect-data",
            "-u",
            "craft-dashboard",
            "-n",
            "100",
            "--no-pager",
            "--output",
            "short",
        )
        assert seen["kwargs"] == {
            "stdout": admin_routes.asyncio.subprocess.PIPE,
            "stderr": admin_routes.asyncio.subprocess.PIPE,
        }
