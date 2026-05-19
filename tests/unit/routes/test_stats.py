"""Tests for the stats routes."""

from types import SimpleNamespace

from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from fastapi.testclient import TestClient


class _EmptyStatsSession:
    async def execute(self, _query):
        return []


class _TrendStatsSession:
    async def execute(self, _query):
        return [SimpleNamespace(name="test-project")]


async def _override_empty_stats_db_session():
    yield _EmptyStatsSession()


async def _override_trend_stats_db_session():
    yield _TrendStatsSession()


class TestStatsRoutes:
    """Tests for stats routes."""

    def test_dependencies_page(self) -> None:
        """GET /stats/dependencies returns HTML."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_empty_stats_db_session

        with TestClient(app) as client:
            response = client.get("/stats/dependencies")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_releases_page(self) -> None:
        """GET /stats/releases returns HTML."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_empty_stats_db_session

        with TestClient(app) as client:
            response = client.get("/stats/releases")

        assert response.status_code == 200

    def test_trends_page(self) -> None:
        """GET /stats/trends returns HTML."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_trend_stats_db_session

        with TestClient(app) as client:
            response = client.get("/stats/trends")

        assert response.status_code == 200

    def test_stats_index_redirects(self) -> None:
        """GET /stats redirects to /stats/dependencies."""
        app = create_app()

        with TestClient(app, follow_redirects=False) as client:
            response = client.get("/stats")

        assert response.status_code in (301, 302, 307)
