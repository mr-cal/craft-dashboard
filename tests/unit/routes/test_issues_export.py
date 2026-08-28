"""Tests for issue JSON export."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.views import IssueFilters, IssueQueryResult, IssueView
from craft_dashboard.repositories.issue_repository import IssueRepository
from fastapi.testclient import TestClient


class _IssueSession:
    async def execute(self, _query):
        return [SimpleNamespace(name="snapcraft")]


async def _override_issue_db_session():
    yield _IssueSession()


class TestIssueExport:
    """Tests for the JSON export endpoint and control."""

    def test_issue_list_includes_export_json_link(self) -> None:
        """Issues page includes a JSON export link."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_issue_db_session

        with (
            patch.object(
                IssueRepository,
                "search",
                AsyncMock(
                    return_value=IssueQueryResult(
                        issues=[], total_count=0, total_pages=1, page=1
                    )
                ),
            ),
            patch.object(
                IssueRepository, "get_project_names", AsyncMock(return_value=[])
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/issues")

        assert response.status_code == 200
        assert 'id="export-json-link"' in response.text
        assert "/issues/export" in response.text

    def test_issue_export_returns_filtered_issue_data(self) -> None:
        """The export endpoint returns all matching issues as JSON."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_issue_db_session
        issue = IssueView(
            id=123,
            project_name="snapcraft",
            source="github",
            external_id="456",
            title="Add core24 support",
            author="maintainer",
            issue_type="issue",
            state="open",
            url="https://example.invalid/issues/456",
            summary="Summary",
            suggested_action="needs_review",
            suggested_action_reason="Needs a maintainer pass.",
            scores={"staleness": 72.5, "confidence": 65.0},
            age_days=14,
            labels=["bug", "core24"],
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            updated_at=datetime(2025, 1, 2, tzinfo=UTC),
            author_is_maintainer=True,
            author_is_bot=False,
            staleness=72.5,
            complexity=None,
            support_request=None,
            confidence=65.0,
            impact=None,
            quick_win=None,
        )
        export_result = IssueQueryResult(
            issues=[issue], total_count=1, total_pages=1, page=1
        )

        with patch.object(
            IssueRepository, "search", AsyncMock(return_value=export_result)
        ) as search:
            with TestClient(app) as client:
                response = client.get(
                    "/issues/export?project=snapcraft&state=open&type=issue&author_role=maintainer&action=needs_review&search=core24&sort=age"
                )

        assert response.status_code == 200
        assert response.headers["content-disposition"] == (
            'attachment; filename="issues-export.json"'
        )
        assert response.json() == [
            {
                "id": 123,
                "project_name": "snapcraft",
                "source": "github",
                "external_id": "456",
                "title": "Add core24 support",
                "author": "maintainer",
                "issue_type": "issue",
                "state": "open",
                "url": "https://example.invalid/issues/456",
                "summary": "Summary",
                "suggested_action": "needs_review",
                "suggested_action_reason": "Needs a maintainer pass.",
                "scores": {"staleness": 72.5, "confidence": 65.0},
                "age_days": 14,
                "labels": ["bug", "core24"],
                "created_at": "2025-01-01T00:00:00+00:00",
                "updated_at": "2025-01-02T00:00:00+00:00",
                "author_is_maintainer": True,
                "author_is_bot": False,
                "staleness": 72.5,
                "complexity": None,
                "support_request": None,
                "confidence": 65.0,
                "impact": None,
                "quick_win": None,
                "has_related_links": False,
            }
        ]
        assert search.await_args.args[0] == IssueFilters(
            project="snapcraft",
            source="",
            state="open",
            issue_type="issue",
            action="needs_review",
            author_role="maintainer",
            sort_by="age",
            page=1,
            search="core24",
            items_per_page=0,
            llm_status="",
        )
