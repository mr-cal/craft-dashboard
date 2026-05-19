"""Tests for the FastAPI application factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from craft_dashboard.app import create_app


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


class TestCreateApp:
    """Tests for create_app."""

    def test_app_returns_fastapi_instance(self) -> None:
        """create_app returns a FastAPI application."""
        app = create_app()
        assert app.title == "craft-dashboard"

    def test_health_endpoint(self) -> None:
        """The /health endpoint returns 200."""
        app = create_app()
        app.router.lifespan_context = _noop_lifespan
        client = TestClient(app)

        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_index_page(self) -> None:
        """The index page returns HTML with dashboard content."""
        app = create_app()
        app.router.lifespan_context = _noop_lifespan

        with patch("craft_dashboard.routes.dashboard.get_db_session") as mock_dep:
            mock_session = AsyncMock()
            mock_result = AsyncMock()
            mock_result.scalars.return_value.all.return_value = []
            mock_session.execute.return_value = mock_result
            mock_session.scalar.return_value = 0

            async def fake_session():
                yield mock_session

            app.dependency_overrides[
                __import__(
                    "craft_dashboard.dependencies", fromlist=["get_db_session"]
                ).get_db_session
            ] = fake_session

            client = TestClient(app)
            response = client.get("/")

            assert response.status_code == 200
            assert "craft-dashboard" in response.text
