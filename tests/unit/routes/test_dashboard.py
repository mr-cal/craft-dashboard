"""Tests for the dashboard routes."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


class TestDashboardIndex:
    """Tests for the dashboard index route."""

    def test_index_returns_html(self) -> None:
        """GET / returns HTML with dashboard content."""
        app = create_app()
        app.router.lifespan_context = _noop_lifespan

        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result
        mock_session.scalar.return_value = 0

        async def fake_session():
            yield mock_session

        app.dependency_overrides[get_db_session] = fake_session

        with TestClient(app) as client:
            response = client.get("/")

            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
            assert "Dashboard" in response.text
