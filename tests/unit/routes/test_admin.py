"""Tests for admin routes."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace

from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from fastapi import FastAPI
from fastapi.testclient import TestClient

_ADMIN_TOKEN = "test-token-123"


class _EmptyResult:
    def scalars(self):
        return []

    def __iter__(self):
        return iter(())


class _AdminSession:
    async def scalar(self, _query):
        return 0

    async def execute(self, _query):
        return _EmptyResult()

    async def commit(self) -> None:
        return None


async def _override_admin_db_session():
    yield _AdminSession()


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


def _create_admin_app():
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.state.settings = SimpleNamespace(admin_token=_ADMIN_TOKEN, refresh_age_days=7)
    app.dependency_overrides[get_db_session] = _override_admin_db_session
    return app


class TestAdminRoutes:
    """Tests for admin routes."""

    def test_admin_refresh_requires_auth(self) -> None:
        """POST /admin/refresh returns 401 without token."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.post("/admin/refresh")

        assert response.status_code == 401

    def test_admin_refresh_rejects_bad_token(self) -> None:
        """POST /admin/refresh returns 401 with wrong token."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.post(
                "/admin/refresh",
                headers={"Authorization": "Bearer wrong-token"},
            )

        assert response.status_code == 401

    def test_admin_health_requires_auth(self) -> None:
        """GET /admin/health returns 401 without token."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.get("/admin/health")

        assert response.status_code == 401


class TestAdminRefreshWithAuth:
    """Authenticated tests for refresh and re-evaluate routes."""

    def test_refresh_with_valid_token(self) -> None:
        """POST /admin/refresh accepts the configured admin token."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.post(
                "/admin/refresh",
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

        assert response.status_code == 202
        assert response.json()["status"] == "refresh_queued"

    def test_re_evaluate_requires_auth(self) -> None:
        """POST /admin/re-evaluate returns 401 without token."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.post("/admin/re-evaluate")

        assert response.status_code == 401

    def test_re_evaluate_with_valid_token(self) -> None:
        """POST /admin/re-evaluate accepts the configured admin token."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.post(
                "/admin/re-evaluate",
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

        assert response.status_code == 202
        assert response.json()["status"] == "evaluation_queued"


class TestAdminDistribute:
    """Tests for refresh schedule distribution."""

    def test_distribute_requires_auth(self) -> None:
        """POST /admin/distribute returns 401 without token."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.post("/admin/distribute")

        assert response.status_code == 401

    def test_distribute_empty_schedules(self) -> None:
        """POST /admin/distribute reports zero schedules when none exist."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.post(
                "/admin/distribute",
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

        assert response.status_code == 200
        assert response.json()["count"] == 0


class TestAdminLogs:
    """Tests for the admin logs endpoint."""

    def test_logs_requires_auth(self) -> None:
        """GET /admin/logs returns 401 without token."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.get("/admin/logs")

        assert response.status_code == 401

    def test_logs_returns_text(self) -> None:
        """GET /admin/logs returns plain text when authorized."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.get(
                "/admin/logs",
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]


class TestAdminPage:
    """Tests for the admin dashboard page."""

    def test_admin_page_no_auth_required(self) -> None:
        """GET /admin renders without admin authentication."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.get("/admin")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Admin" in response.text
