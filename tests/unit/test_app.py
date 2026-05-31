"""Tests for the FastAPI application factory."""

import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from craft_dashboard.app import JSONFormatter, create_app, lifespan
from craft_dashboard.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient


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

        mock_session = AsyncMock()

        async def fake_session():
            yield mock_session

        app.dependency_overrides[
            __import__(
                "craft_dashboard.dependencies", fromlist=["get_db_session"]
            ).get_db_session
        ] = fake_session

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": "ok"}

    def test_404_handler_returns_html(self) -> None:
        """The 404 handler returns an HTML error page."""
        app = create_app()
        app.router.lifespan_context = _noop_lifespan
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/nonexistent-page-xyz")

        assert response.status_code == 404
        assert "text/html" in response.headers.get("content-type", "")

    def test_500_handler_is_registered(self) -> None:
        """The 500 error handler is registered."""
        app = create_app()
        assert 500 in app.exception_handlers

    def test_index_page(self) -> None:
        """The index page returns HTML with dashboard content."""
        app = create_app()
        app.router.lifespan_context = _noop_lifespan

        with patch("craft_dashboard.routes.dashboard.get_db_session"):
            # Mock the result of execute() - use MagicMock not AsyncMock
            mock_execute_result = MagicMock()
            mock_execute_result.scalars.return_value.all.return_value = []
            mock_execute_result.scalar.return_value = None  # For Snapshot queries

            mock_session = AsyncMock()
            mock_session.execute.return_value = mock_execute_result
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

    async def test_lifespan_logs_warnings_for_missing_required_secrets(
        self, caplog
    ) -> None:
        """Startup logs warnings when required secrets are missing."""
        app = FastAPI()
        app.state.settings = Settings(
            database_url="postgresql+asyncpg://localhost/test",
            admin_token="",
            github_token="",
            eval_api_token="",
            config_file="craft-dashboard.toml",
        )
        engine = MagicMock()
        engine.dispose = AsyncMock()

        with (
            patch("craft_dashboard.app.get_engine", return_value=engine),
            patch("craft_dashboard.app.get_session_factory", return_value=MagicMock()),
            patch("craft_dashboard.app.set_session_factory"),
            patch("craft_dashboard.app.load_config", return_value=MagicMock()),
            caplog.at_level(logging.WARNING),
        ):
            async with lifespan(app):
                pass

        assert caplog.messages == [
            "⚠️  ADMIN_TOKEN is not set. Admin endpoints will reject all requests.",
            "⚠️  GITHUB_TOKEN is not set. Data collection will fail.",
            "⚠️  EVAL_API_TOKEN is not set. Eval API endpoints will reject all requests.",
        ]

    def test_json_formatter_formats_log_record(self) -> None:
        """JSONFormatter produces valid JSON."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["message"] == "hello world"
        assert data["level"] == "INFO"
        assert "timestamp" in data

    def test_rate_limiter_is_configured(self) -> None:
        """Rate limiter is set up on the app."""
        app = create_app()
        assert hasattr(app.state, "limiter")
