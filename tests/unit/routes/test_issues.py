"""Tests for the issue triage routes."""

from types import SimpleNamespace
from unittest.mock import patch

from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.routes import issues as issues_routes
from fastapi.testclient import TestClient


class _IssueSession:
    async def execute(self, _query):
        return [SimpleNamespace(name="test-project")]


async def _override_issue_db_session():
    yield _IssueSession()


class TestIssueList:
    """Tests for the issue list route."""

    def test_issues_page_returns_html(self) -> None:
        """GET /issues returns HTML."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_issue_db_session

        with patch.object(issues_routes, "_query_issues", return_value=([], 1)):
            with TestClient(app) as client:
                response = client.get("/issues")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_issues_page_has_filters(self) -> None:
        """Issues page includes filter controls."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_issue_db_session

        with patch.object(issues_routes, "_query_issues", return_value=([], 1)):
            with TestClient(app) as client:
                response = client.get("/issues")

        assert response.status_code == 200
        assert "hx-get" in response.text or "filter" in response.text.lower()

    def test_issues_page_includes_htmx_loading_indicator(self) -> None:
        """Issues page includes an HTMX loading indicator for table refreshes."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_issue_db_session

        with patch.object(issues_routes, "_query_issues", return_value=([], 1)):
            with TestClient(app) as client:
                response = client.get("/issues")

        assert response.status_code == 200
        assert 'id="loading-indicator"' in response.text
        assert "Loading..." in response.text
        assert response.text.count('hx-indicator="#loading-indicator"') == 3
