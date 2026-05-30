"""Tests for shareable issue filter URLs."""

from types import SimpleNamespace
from unittest.mock import patch

from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.views import IssueQueryResult
from craft_dashboard.repositories.issue_repository import IssueRepository
from fastapi.testclient import TestClient


class _IssueSession:
    async def execute(self, _query):
        return [SimpleNamespace(name="snapcraft")]


async def _override_issue_db_session():
    yield _IssueSession()


EMPTY_QUERY_RESULT = IssueQueryResult(issues=[], total_count=0, total_pages=1, page=1)


class TestIssueShareableUrls:
    """Tests for the full issues page shareable URL behavior."""

    def test_issue_list_respects_query_parameters(self) -> None:
        """The full issues page renders current filter values from query params."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_issue_db_session

        with (
            patch.object(IssueRepository, "search", return_value=EMPTY_QUERY_RESULT),
            patch.object(
                IssueRepository,
                "get_project_names",
                return_value=["snapcraft", "charmcraft"],
            ),
        ):
            with TestClient(app) as client:
                response = client.get(
                    "/issues?project=snapcraft&state=closed&type=issue&author_role=maintainer&action=needs_review&scores=complexity,readiness&search=core24&per_page=250&sort=age"
                )

        assert response.status_code == 200
        assert 'name="project" id="project-hidden" value="snapcraft"' in response.text
        assert 'name="state" id="state-hidden" value="closed"' in response.text
        assert 'name="type" id="type-hidden" value="issue"' in response.text
        assert 'name="author_role" id="role-hidden" value="maintainer"' in response.text
        assert 'name="action" id="action-hidden" value="needs_review"' in response.text
        assert (
            'name="scores" id="scores-hidden" value="complexity,readiness"'
            in response.text
        )
        assert 'value="core24"' in response.text
        assert '<option value="250" selected>' in response.text
        assert 'name="sort" value="age"' in response.text

    def test_issue_list_includes_shareable_url_sync_script(self) -> None:
        """The issues page includes client-side history syncing for filters."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_issue_db_session

        with (
            patch.object(IssueRepository, "search", return_value=EMPTY_QUERY_RESULT),
            patch.object(
                IssueRepository, "get_project_names", return_value=["snapcraft"]
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/issues")

        assert response.status_code == 200
        assert 'src="/static/js/issues-filters.js"' in response.text
