"""Integration tests for admin API endpoints with real DB."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.config import DashboardConfig
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.project import Project
from craft_dashboard.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


@pytest.fixture
def app_with_db(test_db_session: AsyncSession) -> tuple[FastAPI, str]:
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.state.config = DashboardConfig()
    app.state.settings = Settings()
    app.state.settings.admin_token = "test-admin-token"
    token = "test-admin-token"

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
                Project(name="admin-project", category="application", github_org="canonical")
            )
            await test_db_session.commit()

        import asyncio  # noqa: PLC0415

        asyncio.get_event_loop().run_until_complete(_seed())

        response = client.get("/admin")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "<h2>Admin</h2>" in response.text

    def test_admin_page_empty_db(self, client: TestClient) -> None:
        """The admin page still renders when the database is empty."""
        response = client.get("/admin")

        assert response.status_code == 200
        assert "No evaluations yet." in response.text


class TestAdminHealthIntegration:
    """Integration tests for the admin health endpoint."""

    def test_health_ok(self, client: TestClient, app_with_db: tuple[FastAPI, str]) -> None:
        """Health returns ok with an empty but reachable DB."""
        _, token = app_with_db

        response = client.get("/admin/health", headers={"Authorization": f"Bearer {token}"})

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


class TestAdminRefreshIntegration:
    """Integration tests for refresh and re-evaluate endpoints."""

    def test_refresh_with_valid_token(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
    ) -> None:
        """Refresh accepts a valid bearer token and queues work."""
        _, token = app_with_db

        response = client.post("/admin/refresh", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 202
        assert response.json()["status"] == "refresh_queued"

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

        response = client.get("/admin/logs", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]

    def test_logs_returns_content(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
    ) -> None:
        """Logs always returns some body text, even without journalctl output."""
        _, token = app_with_db

        response = client.get("/admin/logs", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert response.text.strip() != ""
