"""Tests for the issue detail route."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.views import IssueQueryResult, IssueView
from craft_dashboard.repositories.issue_repository import IssueRepository
from craft_dashboard.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _IssueSession:
    async def execute(self, _query):
        return _EmptyResult()


class _EmptyResult:
    def scalars(self):
        return []

    def first(self):
        return None


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


@pytest.fixture
def test_client() -> AsyncGenerator[TestClient, None]:
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.state.settings = Settings(_env_file=None)

    async def _override_session() -> AsyncGenerator[_IssueSession, None]:
        yield _IssueSession()

    app.dependency_overrides[get_db_session] = _override_session

    with TestClient(app) as client:
        yield client


_DETAIL = {
    "id": 101,
    "project_name": "snapcraft",
    "source": "github",
    "external_id": "321",
    "title": "Support core24 builds end to end",
    "body": "Steps to reproduce\n1. Build\n2. Observe failure",
    "state": "open",
    "author": "sergio-cazzolato",
    "labels": ["bug", "core24"],
    "issue_type": "issue",
    "created_at": "2025-01-10T12:00:00+00:00",
    "updated_at": "2025-01-12T12:00:00+00:00",
    "closed_at": None,
    "url": None,
    "summary": "Regression in the core24 build pipeline.",
    "suggested_action": "needs_review",
    "suggested_action_reason": "Recent failures need maintainer attention.",
    "scores": {"staleness": 0.2, "complexity": 0.7},
    "evaluation_history": [
        {
            "summary": "Regression in the core24 build pipeline.",
            "suggested_action": "needs_review",
            "suggested_action_reason": "Recent failures need maintainer attention.",
            "scores": {"staleness": 0.2, "complexity": 0.7},
            "evaluated_at": "2025-01-12T15:00:00+00:00",
            "model_name": "gpt-4.1",
            "llm_backend": "openai",
            "tokens_used": 120,
            "prompt_tokens": 80,
            "completion_tokens": 40,
        },
        {
            "summary": "Earlier summary.",
            "suggested_action": "keep_open",
            "suggested_action_reason": "Still active.",
            "scores": {"staleness": 0.1},
            "evaluated_at": "2025-01-11T15:00:00+00:00",
            "model_name": "gpt-4o-mini",
            "llm_backend": "openai",
            "tokens_used": 90,
            "prompt_tokens": 60,
            "completion_tokens": 30,
        },
    ],
}


class TestIssueDetailRoute:
    def test_issue_detail_renders_issue_and_evaluation_history(
        self, test_client: TestClient
    ) -> None:
        with patch.object(
            IssueRepository,
            "get_issue_detail",
            AsyncMock(return_value=_DETAIL),
        ) as get_issue_detail:
            response = test_client.get("/issues/snapcraft/321")

        assert response.status_code == 200
        get_issue_detail.assert_awaited_once_with("snapcraft", "321")
        assert "Support core24 builds end to end" in response.text
        assert "Regression in the core24 build pipeline." in response.text
        assert "Earlier summary." in response.text
        assert "sergio-cazzolato" in response.text
        assert "https://github.com/canonical/snapcraft/issues/321" in response.text
        assert "Back to issue list" in response.text

    def test_issue_detail_returns_404_for_unknown_issue(
        self, test_client: TestClient
    ) -> None:
        with patch.object(
            IssueRepository,
            "get_issue_detail",
            AsyncMock(return_value=None),
        ):
            response = test_client.get("/issues/snapcraft/404")

        assert response.status_code == 404

    def test_issue_detail_uses_launchpad_issue_url(
        self, test_client: TestClient
    ) -> None:
        detail = dict(_DETAIL, source="launchpad", project_name="craft-parts")

        with patch.object(
            IssueRepository,
            "get_issue_detail",
            AsyncMock(return_value=detail),
        ):
            response = test_client.get("/issues/craft-parts/321")

        assert response.status_code == 200
        assert "https://bugs.launchpad.net/craft-parts/+bug/321" in response.text

    def test_issue_detail_renders_activity_history(
        self, test_client: TestClient
    ) -> None:
        history = [
            {
                "change_type": "review_approved",
                "title": "Support core24 builds end to end",
                "occurred_at": "2025-01-12T14:00:00+00:00",
            },
            {
                "change_type": "opened",
                "title": "Support core24 builds end to end",
                "occurred_at": "2025-01-10T12:00:00+00:00",
            },
        ]
        with (
            patch.object(
                IssueRepository,
                "get_issue_detail",
                AsyncMock(return_value=_DETAIL),
            ),
            patch.object(
                IssueRepository,
                "get_issue_activity_history",
                AsyncMock(return_value=history),
            ) as get_history,
        ):
            response = test_client.get("/issues/snapcraft/321")

        assert response.status_code == 200
        get_history.assert_awaited_once_with("snapcraft", "321")
        assert "Update history" in response.text
        assert "review approved" in response.text
        assert "opened" in response.text

    def test_issue_detail_shows_no_history_message_when_empty(
        self, test_client: TestClient
    ) -> None:
        with (
            patch.object(
                IssueRepository,
                "get_issue_detail",
                AsyncMock(return_value=_DETAIL),
            ),
            patch.object(
                IssueRepository,
                "get_issue_activity_history",
                AsyncMock(return_value=[]),
            ),
        ):
            response = test_client.get("/issues/snapcraft/321")

        assert response.status_code == 200
        assert "No update history recorded yet" in response.text

    def test_issue_list_titles_link_to_issue_detail(
        self, test_client: TestClient
    ) -> None:
        issue = IssueView(
            id=101,
            project_name="snapcraft",
            source="github",
            external_id="321",
            title="Support core24 builds end to end",
            author="sergio-cazzolato",
            issue_type="issue",
            state="open",
            url="https://github.com/canonical/snapcraft/issues/321",
            summary="Regression in the core24 build pipeline.",
            suggested_action="needs_review",
            suggested_action_reason="Recent failures need maintainer attention.",
            scores={"staleness": 0.2},
        )
        result = IssueQueryResult(issues=[issue], total_count=1, total_pages=1, page=1)

        with (
            patch.object(IssueRepository, "search", AsyncMock(return_value=result)),
            patch.object(
                IssueRepository,
                "get_project_names",
                AsyncMock(return_value=["snapcraft"]),
            ),
        ):
            response = test_client.get("/issues")

        assert response.status_code == 200
        assert 'href="/issues/snapcraft/321"' in response.text


_RELATED = [
    {
        "id": 202,
        "external_id": "400",
        "title": "Similar bug in core22 builds",
        "url": "https://github.com/canonical/snapcraft/issues/400",
        "state": "open",
        "project_name": "snapcraft",
        "summary": "Closely related build failure.",
        "similarity": 0.91,
    }
]


class TestRelatedIssuesSection:
    def test_related_issues_shown_when_present(self, test_client: TestClient) -> None:
        with (
            patch.object(
                IssueRepository,
                "get_issue_detail",
                AsyncMock(return_value=_DETAIL),
            ),
            patch.object(
                IssueRepository,
                "find_similar_issues",
                AsyncMock(return_value=_RELATED),
            ),
        ):
            response = test_client.get("/issues/snapcraft/321")

        assert response.status_code == 200
        assert "Related Issues" in response.text
        assert "Similar bug in core22 builds" in response.text
        assert "91%" in response.text

    def test_related_issues_empty_no_embedding_shows_notice(
        self, test_client: TestClient
    ) -> None:
        """When the issue has no embedding, show the 'no embedding' notice."""
        with (
            patch.object(
                IssueRepository,
                "get_issue_detail",
                AsyncMock(return_value=_DETAIL),
            ),
            patch.object(
                IssueRepository,
                "find_similar_issues",
                AsyncMock(return_value=[]),
            ),
        ):
            response = test_client.get("/issues/snapcraft/321")

        assert response.status_code == 200
        assert "Related Issues" in response.text
        assert "No embedding available" in response.text

    def test_related_issues_empty_with_embedding_shows_threshold_notice(
        self, test_client: TestClient
    ) -> None:
        """When the issue has an embedding but no similar results, show threshold notice."""
        detail_with_embedding = {
            **_DETAIL,
            "evaluation_history": [
                {**_DETAIL["evaluation_history"][0], "has_embedding": True},
                *_DETAIL["evaluation_history"][1:],
            ],
        }
        with (
            patch.object(
                IssueRepository,
                "get_issue_detail",
                AsyncMock(return_value=detail_with_embedding),
            ),
            patch.object(
                IssueRepository,
                "find_similar_issues",
                AsyncMock(return_value=[]),
            ),
        ):
            response = test_client.get("/issues/snapcraft/321")

        assert response.status_code == 200
        assert "Related Issues" in response.text
        assert "No related issues found above the similarity threshold" in response.text
