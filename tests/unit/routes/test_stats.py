"""Tests for the stats routes."""

from types import SimpleNamespace

from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.routes.stats import _version_key
from fastapi.testclient import TestClient


class _EmptyStatsResult:
    def all(self):
        return []

    def scalars(self):
        return self

    def scalar(self):
        return None

    def __iter__(self):
        return iter([])


class _EmptyStatsSession:
    async def execute(self, _query):
        return _EmptyStatsResult()

    async def scalars(self, _query):
        return _EmptyStatsResult()

    async def scalar(self, _query):
        return None


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

    def test_triage_page(self) -> None:
        """GET /stats/triage returns HTML."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_empty_stats_db_session

        with TestClient(app) as client:
            response = client.get("/stats/triage")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Triage" in response.text
        assert 'data-paginate="20"' in response.text
        assert (
            'data-tooltip="Contributor PRs awaiting first maintainer review"'
            in response.text
        )

    def test_trends_page(self) -> None:
        """GET /stats/trends returns accessible HTML with loading state."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_trend_stats_db_session

        with TestClient(app) as client:
            response = client.get("/stats/trends")

        assert response.status_code == 200
        assert (
            '<link rel="icon" type="image/x-icon" href="/static/favicon.ico" />'
            in response.text
        )
        assert 'id="trends-loading"' in response.text
        assert 'class="p-tabs__link is-active"' in response.text
        assert 'src="/static/js/trends.js?v=' in response.text
        assert (
            'aria-label="Line chart showing open issues over time for selected projects"'
            in response.text
        )
        assert (
            'aria-label="Line chart showing median issue age over time for selected projects"'
            in response.text
        )
        assert (
            'aria-label="Bar chart showing issues closed per week for selected projects"'
            in response.text
        )
        assert 'id="snapshot-table"' in response.text
        assert "Annual delivery &amp; project state" in response.text
        assert 'name="trend-projects"' in response.text
        # Verify only all-projects is checked by default and not duplicated
        assert (
            response.text.count('value="all-projects"') == 2
        )  # 1 in option, 1 in hidden input
        assert 'value="all-projects" checked' in response.text

    def test_stats_index_redirects(self) -> None:
        """GET /stats redirects to /stats/dependencies."""
        app = create_app()

        with TestClient(app, follow_redirects=False) as client:
            response = client.get("/stats")

        assert response.status_code in (301, 302, 307)


class TestVersionKey:
    """Unit tests for the _version_key helper function."""

    def test_simple_version(self) -> None:
        assert _version_key("4.2") == (4, 2)

    def test_three_part_version(self) -> None:
        assert _version_key("4.2.1") == (4, 2, 1)

    def test_ordering(self) -> None:
        assert _version_key("4.2") > _version_key("4.1")
        assert _version_key("4.10") > _version_key("4.2")
        assert _version_key("5.0") > _version_key("4.99")

    def test_non_numeric_part(self) -> None:
        assert _version_key("main") == (0,)
