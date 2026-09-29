"""Tests for the issue detail route."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.llm.content_hash import compute_content_hash
from craft_dashboard.llm.evaluator import (
    OPEN_ISSUE_EVAL_VERSION,
    OPEN_PR_EVAL_VERSION,
)
from craft_dashboard.models.views import IssueQueryResult, IssueView
from craft_dashboard.repositories.issue_link_repository import IssueLinkRepository
from craft_dashboard.repositories.issue_repository import IssueRepository
from craft_dashboard.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.helpers.html import parse_html


class _IssueSession:
    async def execute(self, _query):
        return _EmptyResult()


class _EmptyScalars(list):
    """A list that also supports the ``.all()`` call SQLAlchemy's real
    ``ScalarResult`` exposes, so mocked callers can use either
    ``for x in session.execute(...).scalars()`` or
    ``session.execute(...).scalars().all()`` (as ``IssueLinkRepository``
    does) against the same empty result.
    """

    def all(self) -> list:
        """Return self, matching ``ScalarResult.all()``."""
        return self


class _EmptyResult:
    def scalars(self):
        return _EmptyScalars()

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
    "scores": {"actionability": 0.8, "complexity": 0.7},
    "evaluation_history": [
        {
            "summary": "Regression in the core24 build pipeline.",
            "suggested_action": "needs_review",
            "suggested_action_reason": "Recent failures need maintainer attention.",
            "scores": {"actionability": 0.8, "complexity": 0.7},
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
            "scores": {"actionability": 0.7},
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
        doc = parse_html(response)
        assert doc.text("h2") == "Support core24 builds end to end"
        history = doc.section_by_heading("Evaluation history")
        rows = history.texts("tbody tr")
        assert "Regression in the core24 build pipeline." in rows[0]
        assert "Earlier summary." in rows[1]
        metadata = doc.section_by_heading("Metadata")
        assert metadata.texts("dd")[4] == "sergio-cazzolato"
        assert "https://github.com/canonical/snapcraft/issues/321" in doc.links()
        assert any("Back to issue list" in text for text in doc.texts("a"))

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
        links = parse_html(response).links()
        assert "https://bugs.launchpad.net/craft-parts/+bug/321" in links

    def test_snapcraft_launchpad_redirects_to_slug(
        self, test_client: TestClient
    ) -> None:
        response = test_client.get(
            "/issues/snapcraft%20%28launchpad%29/1876370",
            follow_redirects=False,
        )
        assert response.status_code == 308
        assert response.headers["location"] == "/issues/snapcraft-launchpad/1876370"

    def test_snapcraft_launchpad_slug_renders_with_db_name(
        self, test_client: TestClient
    ) -> None:
        detail = dict(
            _DETAIL,
            project_name="snapcraft (launchpad)",
            source="launchpad",
            external_id="1876370",
        )
        with patch.object(
            IssueRepository,
            "get_issue_detail",
            AsyncMock(return_value=detail),
        ) as get_detail:
            response = test_client.get("/issues/snapcraft-launchpad/1876370")

        assert response.status_code == 200
        get_detail.assert_awaited_once_with("snapcraft (launchpad)", "1876370")
        doc = parse_html(response)
        metadata = doc.section_by_heading("Metadata")
        assert metadata.texts("dd")[0] == "snapcraft (launchpad)"
        assert "https://bugs.launchpad.net/snapcraft/+bug/1876370" in doc.links()

    def test_issue_list_slugifies_snapcraft_launchpad_url(
        self, test_client: TestClient
    ) -> None:
        issue = IssueView(
            id=102,
            project_name="snapcraft (launchpad)",
            source="launchpad",
            external_id="1876370",
            title="Snapcraft LP bug",
            author="contributor",
            issue_type="issue",
            state="open",
            url="https://bugs.launchpad.net/snapcraft/+bug/1876370",
            summary="Snapcraft launchpad bug summary.",
            suggested_action="needs_triage",
            suggested_action_reason="Needs triage.",
            scores={"actionability": 0.8},
        )
        result = IssueQueryResult(issues=[issue], total_count=1, total_pages=1, page=1)

        with (
            patch.object(IssueRepository, "search", AsyncMock(return_value=result)),
            patch.object(
                IssueRepository,
                "get_project_names",
                AsyncMock(return_value=["snapcraft (launchpad)"]),
            ),
        ):
            response = test_client.get("/issues")

        assert response.status_code == 200
        assert "/issues/snapcraft-launchpad/1876370" in parse_html(response).links()

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
        update_history = parse_html(response).section_by_heading("Update history")
        assert update_history.texts("tbody tr td:nth-of-type(2)") == [
            "review approved",
            "opened",
        ]

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
        update_history = parse_html(response).section_by_heading("Update history")
        assert update_history.text("p") == "No update history recorded yet."
        assert update_history.count("tbody tr") == 0

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
            scores={"actionability": 0.8},
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
        assert "/issues/snapcraft/321" in parse_html(response).links()


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
        related = parse_html(response).section_by_heading("Related issues")
        assert related.count("tbody tr") == 1
        assert "Similar bug in core22 builds" in related.text("tbody tr")
        assert related.text(".similarity-label") == "91%"

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
        related = parse_html(response).section_by_heading("Related issues")
        assert related.count("tbody tr") == 0
        assert related.has_text("No embedding available", "p")

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
        related = parse_html(response).section_by_heading("Related issues")
        assert related.count("tbody tr") == 0
        assert (
            related.text("p")
            == "No related issues found above the similarity threshold."
        )


class TestOutdatedEvaluationNotice:
    def test_outdated_notice_not_shown_when_up_to_date_with_comments(
        self, test_client: TestClient
    ) -> None:
        comments = [
            {
                "author": "alice",
                "body": "comment 1",
                "created_at": "2025-01-11T10:00:00Z",
            }
        ]
        content_hash = compute_content_hash(
            "Support core24 builds end to end",
            "Steps to reproduce\n1. Build\n2. Observe failure",
            "open",
            ["bug", "core24"],
            comments=comments,
        )
        detail = {
            **_DETAIL,
            "comments": comments,
            "content_hash": content_hash,
            "evidence_generation": 1,
            "evaluation_history": [
                {
                    **_DETAIL["evaluation_history"][0],
                    "eval_version": OPEN_ISSUE_EVAL_VERSION,
                    "issue_data_hash": content_hash,
                    "evidence_generation": 1,
                }
            ],
        }
        with (
            patch.object(
                IssueRepository,
                "get_issue_detail",
                AsyncMock(return_value=detail),
            ),
            patch.object(
                IssueRepository,
                "get_issue_activity_history",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                IssueRepository,
                "find_similar_issues",
                AsyncMock(return_value=[]),
            ),
        ):
            response = test_client.get("/issues/snapcraft/321")

        assert response.status_code == 200
        assert not parse_html(response).exists(".evaluation-outdated-notice")

    def test_outdated_notice_shown_when_version_is_stale(
        self, test_client: TestClient
    ) -> None:
        detail = {
            **_DETAIL,
            "evaluation_history": [
                {
                    **_DETAIL["evaluation_history"][0],
                    "eval_version": OPEN_ISSUE_EVAL_VERSION - 1,
                    "issue_data_hash": "some-hash",
                }
            ],
        }
        with (
            patch.object(
                IssueRepository,
                "get_issue_detail",
                AsyncMock(return_value=detail),
            ),
            patch.object(
                IssueRepository,
                "get_issue_activity_history",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                IssueRepository,
                "find_similar_issues",
                AsyncMock(return_value=[]),
            ),
        ):
            response = test_client.get("/issues/snapcraft/321")

        assert response.status_code == 200
        assert parse_html(response).count(".evaluation-outdated-notice") == 1

    def test_outdated_notice_for_pr_eval_version(self, test_client: TestClient) -> None:
        pr_detail_current = {
            **_DETAIL,
            "is_pr": True,
            "evaluation_history": [
                {
                    **_DETAIL["evaluation_history"][0],
                    "eval_version": OPEN_PR_EVAL_VERSION,
                    "issue_data_hash": "some-hash",
                }
            ],
            "content_hash": "some-hash",
        }
        with (
            patch.object(
                IssueRepository,
                "get_issue_detail",
                AsyncMock(return_value=pr_detail_current),
            ),
            patch.object(
                IssueRepository,
                "get_issue_activity_history",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                IssueRepository,
                "find_similar_issues",
                AsyncMock(return_value=[]),
            ),
        ):
            response = test_client.get("/issues/snapcraft/321")

        assert response.status_code == 200
        assert not parse_html(response).exists(".evaluation-outdated-notice")

        pr_detail_stale = {
            **pr_detail_current,
            "evaluation_history": [
                {
                    **_DETAIL["evaluation_history"][0],
                    "eval_version": OPEN_PR_EVAL_VERSION - 1,
                    "issue_data_hash": "some-hash",
                }
            ],
        }
        with (
            patch.object(
                IssueRepository,
                "get_issue_detail",
                AsyncMock(return_value=pr_detail_stale),
            ),
            patch.object(
                IssueRepository,
                "get_issue_activity_history",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                IssueRepository,
                "find_similar_issues",
                AsyncMock(return_value=[]),
            ),
        ):
            response = test_client.get("/issues/snapcraft/321")

        assert response.status_code == 200
        assert parse_html(response).count(".evaluation-outdated-notice") == 1

    def test_outdated_notice_shown_when_hash_mismatch(
        self, test_client: TestClient
    ) -> None:
        detail = {
            **_DETAIL,
            "content_hash": "new-hash",
            "evaluation_history": [
                {
                    **_DETAIL["evaluation_history"][0],
                    "eval_version": OPEN_ISSUE_EVAL_VERSION,
                    "issue_data_hash": "old-hash",
                }
            ],
        }
        with (
            patch.object(
                IssueRepository,
                "get_issue_detail",
                AsyncMock(return_value=detail),
            ),
            patch.object(
                IssueRepository,
                "get_issue_activity_history",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                IssueRepository,
                "find_similar_issues",
                AsyncMock(return_value=[]),
            ),
        ):
            response = test_client.get("/issues/snapcraft/321")

        assert response.status_code == 200
        assert parse_html(response).count(".evaluation-outdated-notice") == 1


class TestRelatedLinksSection:
    def test_related_links_external_fallback_when_to_issue_is_none(
        self, test_client: TestClient
    ) -> None:
        mock_link = MagicMock()
        mock_link.kind = "likely_fixed_by"
        mock_link.to_ref = "craft-providers#823"
        mock_link.to_issue = None
        mock_link.confidence = 85
        mock_link.note = None

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
            patch.object(
                IssueRepository,
                "find_similar_issues",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                IssueLinkRepository,
                "get_latest_links_for_issue",
                AsyncMock(return_value=[mock_link]),
            ),
        ):
            response = test_client.get("/issues/snapcraft/321")

        assert response.status_code == 200
        related_work = parse_html(response).section_by_heading("Related work")
        item = related_work.text("li")
        assert item.startswith("Likely Fixed By : craft-providers#823")
        assert "(confidence 85%)" in item
        assert related_work.attr("li a", "href") == (
            "https://github.com/canonical/craft-providers/issues/823"
        )
        assert related_work.text("li a") == "craft-providers#823"
