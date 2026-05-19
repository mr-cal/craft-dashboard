"""Tests for the FastAPI application factory."""

from craft_dashboard.app import create_app
from fastapi.testclient import TestClient


class TestCreateApp:
    """Tests for create_app."""

    def test_app_returns_fastapi_instance(self) -> None:
        """create_app returns a FastAPI application."""
        app = create_app()
        assert app.title == "craft-dashboard"

    def test_health_endpoint(self) -> None:
        """The /health endpoint returns 200."""
        app = create_app()
        client = TestClient(app)

        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_index_page(self) -> None:
        """The index page returns HTML."""
        app = create_app()
        client = TestClient(app)

        response = client.get("/")

        assert response.status_code == 200
        assert "craft-dashboard" in response.text
