"""Tests for the issue triage routes."""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.views import IssueFilters, IssueQueryResult
from craft_dashboard.repositories.issue_repository import IssueRepository
from craft_dashboard.routes.issues import (
    ALL_SCORES,
    DEFAULT_SCORES,
    _build_issue_context,
)
from fastapi.testclient import TestClient


class _IssueSession:
    async def execute(self, _query):
        return [SimpleNamespace(name="snapcraft")]


async def _override_issue_db_session():
    yield _IssueSession()


EMPTY_QUERY_RESULT = IssueQueryResult(issues=[], total_count=0, total_pages=1, page=1)


class TestIssueContext:
    """Tests for the shared issue template context builder."""

    async def test_build_issue_context_returns_shared_template_context(self) -> None:
        """The shared helper builds the full template context in one place."""
        filters = IssueFilters(
            project="snapcraft",
            source="github",
            state="open",
            issue_type="issue",
            action="needs_review",
            author_role="maintainer",
            sort_by="title",
            page=2,
            search="core24",
            items_per_page=250,
            llm_status="partial_llm",
        )
        result = IssueQueryResult(issues=[], total_count=12, total_pages=3, page=2)

        with (
            patch.object(IssueRepository, "search", AsyncMock(return_value=result)),
            patch.object(
                IssueRepository,
                "get_project_names",
                AsyncMock(return_value=["snapcraft", "charmcraft"]),
            ),
        ):
            context = await _build_issue_context(
                _IssueSession(),
                filters=filters,
                scores="bogus",
            )

        assert context == {
            "issues": [],
            "project_names": ["snapcraft", "charmcraft"],
            "filter_project": "snapcraft",
            "filter_source": "github",
            "filter_state": "open",
            "filter_type": "issue",
            "filter_action": "needs_review",
            "filter_author_role": "maintainer",
            "filter_search": "core24",
            "sort_by": "title",
            "page": 2,
            "total_pages": 3,
            "per_page": 250,
            "filter_scores": "bogus",
            "active_scores": DEFAULT_SCORES.split(","),
            "all_scores": ALL_SCORES,
            "filter_llm_status": "partial_llm",
            "total_count": 12,
        }


class TestIssueList:
    """Tests for the issue list route."""

    def test_issues_page_returns_html(self) -> None:
        """GET /issues returns HTML."""
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
        assert "text/html" in response.headers["content-type"]

    def test_issues_page_has_filters(self) -> None:
        """Issues page includes filter controls."""
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
        assert "hx-get" in response.text or "filter" in response.text.lower()

    def test_issues_page_uses_shared_context_builder(self) -> None:
        """The full page route delegates context building to the shared helper."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_issue_db_session
        context = {
            "issues": [],
            "project_names": ["snapcraft"],
            "filter_project": "snapcraft",
            "filter_source": "github",
            "filter_state": "open",
            "filter_type": "issue",
            "filter_action": "needs_review",
            "filter_author_role": "maintainer",
            "filter_search": "core24",
            "sort_by": "title",
            "page": 2,
            "total_pages": 3,
            "per_page": 250,
            "filter_scores": "readiness",
            "active_scores": ["readiness"],
            "all_scores": ALL_SCORES,
            "filter_llm_status": "partial_llm",
            "total_count": 12,
        }

        with patch(
            "craft_dashboard.routes.issues._build_issue_context",
            AsyncMock(return_value=context),
        ) as build_context:
            with TestClient(app) as client:
                response = client.get(
                    "/issues?project=snapcraft&source=github&type=issue"
                    "&action=needs_review&author_role=maintainer&sort=title"
                    "&page=2&search=core24&per_page=250&scores=readiness"
                    "&llm_status=partial_llm"
                )

        assert response.status_code == 200
        build_context.assert_awaited_once()
        kwargs = build_context.await_args.kwargs
        assert kwargs["scores"] == "readiness"
        assert kwargs["filters"] == IssueFilters(
            project="snapcraft",
            source="github",
            state="open",
            issue_type="issue",
            action="needs_review",
            author_role="maintainer",
            sort_by="title",
            page=2,
            search="core24",
            items_per_page=250,
            llm_status="partial_llm",
        )

    def test_issues_page_includes_htmx_loading_indicator(self) -> None:
        """Issues page includes an HTMX loading indicator for table refreshes."""
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
        assert 'id="loading-indicator"' in response.text
        assert "Loading..." in response.text

        hx_get_count = len(re.findall(r'\bhx-get="', response.text))
        hx_indicator_count = response.text.count('hx-indicator="#loading-indicator"')

        assert hx_get_count > 0
        assert hx_indicator_count == hx_get_count

    def test_issues_page_includes_dark_mode_toggle_and_bootstrap_script(self) -> None:
        """Issues page includes theme controls and early bootstrap logic."""
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
        assert 'id="theme-toggle"' in response.text
        assert 'localStorage.getItem("theme")' in response.text
        assert "prefers-color-scheme: dark" in response.text
        assert "is-dark-theme" in response.text

    def test_issues_page_includes_reusable_toast_system(self) -> None:
        """Issues page includes the shared toast container and trigger hooks."""
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
        assert 'id="toast-container"' in response.text
        assert "showToast(message, type)" in response.text
        assert 'addEventListener("toast"' in response.text
        assert "htmx:responseError" in response.text


class TestIssueTablePartial:
    """Tests for the issue table partial route."""

    def test_issue_table_partial_uses_shared_context_builder(self) -> None:
        """The table partial route delegates context building to the shared helper."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_issue_db_session
        context = {
            "issues": [],
            "project_names": ["snapcraft"],
            "filter_project": "snapcraft",
            "filter_source": "github",
            "filter_state": "open",
            "filter_type": "issue",
            "filter_action": "needs_review",
            "filter_author_role": "maintainer",
            "filter_search": "core24",
            "sort_by": "title",
            "page": 2,
            "total_pages": 3,
            "per_page": 250,
            "filter_scores": "readiness",
            "active_scores": ["readiness"],
            "all_scores": ALL_SCORES,
            "filter_llm_status": "partial_llm",
            "total_count": 12,
        }

        with patch(
            "craft_dashboard.routes.issues._build_issue_context",
            AsyncMock(return_value=context),
        ) as build_context:
            with TestClient(app) as client:
                response = client.get(
                    "/issues/table?project=snapcraft&source=github&type=issue"
                    "&action=needs_review&author_role=maintainer&sort=title"
                    "&page=2&search=core24&per_page=250&scores=readiness"
                    "&llm_status=partial_llm"
                )

        assert response.status_code == 200
        build_context.assert_awaited_once()
        kwargs = build_context.await_args.kwargs
        assert kwargs["scores"] == "readiness"
        assert kwargs["filters"] == IssueFilters(
            project="snapcraft",
            source="github",
            state="open",
            issue_type="issue",
            action="needs_review",
            author_role="maintainer",
            sort_by="title",
            page=2,
            search="core24",
            items_per_page=250,
            llm_status="partial_llm",
        )
