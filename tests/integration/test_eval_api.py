"""Integration tests for eval API endpoints with a real DB."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from craft_dashboard.app import create_app
from craft_dashboard.config import DashboardConfig
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.llm.evaluator import CURRENT_EVAL_VERSION, _compute_content_hash
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.settings import Settings
from fastapi.testclient import TestClient
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import make_evaluation, make_issue, make_project

_TEST_EVAL_TOKEN = "test-eval-token"


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


async def _seed_entities(session: AsyncSession, *entities: object) -> None:
    session.add_all(list(entities))
    await session.commit()


async def _all_evaluations(session: AsyncSession) -> list[LLMEvaluation]:
    result = await session.execute(
        select(LLMEvaluation).order_by(LLMEvaluation.issue_id, LLMEvaluation.id)
    )
    return list(result.scalars())


def _create_eval_app(test_db_session: AsyncSession) -> tuple[FastAPI, str]:
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.state.config = DashboardConfig(maintainers=["alice", "bob"])
    app.state.settings = Settings()
    app.state.settings.eval_api_token = _TEST_EVAL_TOKEN

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield test_db_session

    app.dependency_overrides[get_db_session] = _override
    return app, _TEST_EVAL_TOKEN


class TestEvalNextIntegration:
    """Integration tests for GET /api/eval/next."""

    def test_next_requires_auth(self, test_db_session: AsyncSession) -> None:
        app, _token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get("/api/eval/next")

        assert response.status_code == 401

    def test_next_returns_first_issue_and_locks_it(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(
            id=1,
            project_id=1,
            external_id="42",
            title="Build fails on arm64",
            body="The latest build regressed on arm64 only.",
            author="alice",
            author_is_maintainer=True,
            labels=["bug", "arm64"],
        )
        issue.comments = [{"author": "bob", "body": "I can reproduce this."}]
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )
        app, token = _create_eval_app(test_db_session)
        expected_hash = _compute_content_hash(
            issue.title,
            issue.body,
            issue.state,
            issue.labels,
            issue.comments,
        )

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/next", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 200
        assert response.json() == {
            "issue_id": 1,
            "project_name": "snapcraft",
            "external_id": "42",
            "title": "Build fails on arm64",
            "state": "open",
            "issue_type": "issue",
            "body": "The latest build regressed on arm64 only.",
            "comments": [{"author": "bob", "body": "I can reproduce this."}],
            "labels": ["bug", "arm64"],
            "author": "alice",
            "author_association": "MAINTAINER",
            "created_at": issue.created_at.isoformat(),
            "updated_at": issue.updated_at.isoformat(),
            "current_hash": expected_hash,
            "maintainers": ["alice", "bob"],
            "closing_references": [],
            "pr_details": {},
        }

        evaluations = asyncio.get_event_loop().run_until_complete(
            _all_evaluations(test_db_session)
        )
        assert len(evaluations) == 1
        assert evaluations[0].issue_id == 1
        assert evaluations[0].model_name == "pending"
        assert evaluations[0].latest is True
        assert evaluations[0].eval_locked_until is not None
        assert evaluations[0].eval_locked_until.replace(tzinfo=UTC) > datetime.now(
            tz=UTC
        )

    def test_next_skips_locked_issue(self, test_db_session: AsyncSession) -> None:
        project = make_project(id=1, name="snapcraft")
        locked_issue = make_issue(id=1, project_id=1, external_id="1", title="Locked")
        free_issue = make_issue(id=2, project_id=1, external_id="2", title="Unlocked")
        locked_eval = make_evaluation(
            id=1,
            issue_id=1,
            latest=True,
            eval_locked_until=datetime.now(tz=UTC) + timedelta(minutes=10),
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(
                test_db_session, project, locked_issue, free_issue, locked_eval
            )
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/next", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 200
        assert response.json()["issue_id"] == 2
        assert response.json()["title"] == "Unlocked"

    def test_next_incomplete_does_not_requeue_closed_issue_without_scores(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        closed_issue = make_issue(
            id=1,
            project_id=1,
            external_id="1",
            title="Closed issue",
            state="closed",
        )
        open_issue = make_issue(
            id=2,
            project_id=1,
            external_id="2",
            title="Open issue",
        )
        closed_eval = make_evaluation(
            id=1,
            issue_id=1,
            latest=True,
            scores={},
            suggested_action=None,
            suggested_action_reason=None,
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(
                test_db_session, project, closed_issue, open_issue, closed_eval
            )
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/next?open_only=false&incomplete=true",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 200
        assert response.json()["issue_id"] == 2

    def test_next_skips_unchanged_issue_unless_forced(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, external_id="1", title="Stable issue")
        issue_hash = _compute_content_hash(
            issue.title,
            issue.body,
            issue.state,
            issue.labels,
            issue.comments,
        )
        existing_eval = make_evaluation(
            id=1,
            issue_id=1,
            latest=True,
            summary="This summary is intentionally long enough.",
            suggested_action="keep_open",
            suggested_action_reason="The issue still needs maintainer attention.",
            scores={
                "staleness": 0,
                "complexity": 0,
                "support_request": 0,
                "readiness": 0,
            },
            eval_version=CURRENT_EVAL_VERSION,
            issue_data_hash=issue_hash,
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue, existing_eval)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            skipped = client.get(
                "/api/eval/next",
                headers={"Authorization": f"Bearer {token}"},
            )
            forced = client.get(
                "/api/eval/next?force=true",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert skipped.status_code == 204
        assert forced.status_code == 200
        assert forced.json()["issue_id"] == 1

    def test_next_returns_pr_after_new_review_approval(
        self, test_db_session: AsyncSession
    ) -> None:
        """A PR approval changes the hash even with no new comment, so it's re-queued.

        Regression test for issues/PRs whose review status changed (e.g. a new
        approval) not being picked up by the eval client because the content
        hash didn't account for PR review/CI metadata.
        """
        project = make_project(id=1, name="craft-application")
        issue = make_issue(
            id=1,
            project_id=1,
            external_id="1140",
            issue_type="pull_request",
            title="Fix flaky test",
            state="open",
        )
        # Evaluation was stored before the PR was approved: hash computed
        # without any pr_details (as it would have been prior to this fix, or
        # simply before the approval happened).
        stale_hash = _compute_content_hash(
            issue.title,
            issue.body,
            issue.state,
            issue.labels,
            issue.comments,
        )
        existing_eval = make_evaluation(
            id=1,
            issue_id=1,
            latest=True,
            summary="This summary is intentionally long enough.",
            suggested_action="keep_open",
            suggested_action_reason="Waiting on review.",
            scores={
                "staleness": 0,
                "complexity": 0,
                "support_request": 0,
                "readiness": 0,
            },
            eval_version=CURRENT_EVAL_VERSION,
            issue_data_hash=stale_hash,
        )
        # The PR has since been approved.
        issue.metadata_ = {"review_status": "approved", "review_count": 1}
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue, existing_eval)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/next?open_only=false",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 200
        assert response.json()["issue_id"] == 1
        assert response.json()["pr_details"] == {
            "review_status": "approved",
            "review_count": 1,
        }

    def test_next_response_includes_pr_details(
        self, test_db_session: AsyncSession
    ) -> None:
        """The /api/eval/next payload surfaces PR review/CI metadata."""
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(
            id=1,
            project_id=1,
            external_id="7",
            issue_type="pull_request",
            title="Add feature",
        )
        issue.metadata_ = {
            "review_status": "changes_requested",
            "review_count": 2,
            "ci_passing": ["lint"],
            "ci_failing": ["unit"],
            "ci_pending": [],
            "unresolved_review_comments": 1,
            "diff_additions": 20,
            "diff_deletions": 4,
            "diff_files_changed": 2,
        }
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/next",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 200
        assert response.json()["pr_details"] == issue.metadata_


class TestEvalNextPriorityOrdering:
    """Priority ordering tests for GET /api/eval/next."""

    def test_unevaluated_open_before_unevaluated_closed(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        closed_issue = make_issue(
            id=1,
            project_id=1,
            external_id="1",
            title="Closed issue",
            state="closed",
        )
        open_issue = make_issue(
            id=2,
            project_id=1,
            external_id="2",
            title="Open issue",
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, closed_issue, open_issue)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            self.client = client
            first = self.client.get(
                "/api/eval/next?open_only=false",
                headers={"Authorization": f"Bearer {token}"},
            )
            second = self.client.get(
                "/api/eval/next?open_only=false",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["issue_id"] == 2
        assert second.json()["issue_id"] == 1

    def test_unevaluated_before_old_version(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        old_issue = make_issue(
            id=1,
            project_id=1,
            external_id="1",
            title="Old version issue",
        )
        unevaluated_issue = make_issue(
            id=2,
            project_id=1,
            external_id="2",
            title="Unevaluated issue",
        )
        old_issue_hash = _compute_content_hash(
            old_issue.title,
            old_issue.body,
            old_issue.state,
            old_issue.labels,
            old_issue.comments,
        )
        old_evaluation = make_evaluation(
            id=1,
            issue_id=1,
            latest=True,
            summary="This summary is intentionally long enough.",
            suggested_action="keep_open",
            suggested_action_reason="This still needs attention from maintainers.",
            scores={
                "staleness": 1,
                "complexity": 1,
                "support_request": 1,
                "readiness": 1,
            },
            eval_version=CURRENT_EVAL_VERSION - 1,
            issue_data_hash=old_issue_hash,
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(
                test_db_session,
                project,
                old_issue,
                unevaluated_issue,
                old_evaluation,
            )
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            self.client = client
            first = self.client.get(
                "/api/eval/next", headers={"Authorization": f"Bearer {token}"}
            )
            second = self.client.get(
                "/api/eval/next", headers={"Authorization": f"Bearer {token}"}
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["issue_id"] == 2
        assert second.json()["issue_id"] == 1

    def test_old_version_before_current_version_with_hash_change(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        current_version_issue = make_issue(
            id=1,
            project_id=1,
            external_id="1",
            title="Current version changed",
        )
        old_version_issue = make_issue(
            id=2,
            project_id=1,
            external_id="2",
            title="Old version issue",
        )
        current_version_evaluation = make_evaluation(
            id=1,
            issue_id=1,
            latest=True,
            summary="This summary is intentionally long enough.",
            suggested_action="keep_open",
            suggested_action_reason="This issue changed since the last evaluation.",
            scores={
                "staleness": 1,
                "complexity": 1,
                "support_request": 1,
                "readiness": 1,
            },
            eval_version=CURRENT_EVAL_VERSION,
            issue_data_hash="stale-hash",
        )
        old_version_evaluation = make_evaluation(
            id=2,
            issue_id=2,
            latest=True,
            summary="This summary is intentionally long enough.",
            suggested_action="keep_open",
            suggested_action_reason="This issue needs reevaluation on the old version.",
            scores={
                "staleness": 2,
                "complexity": 2,
                "support_request": 2,
                "readiness": 2,
            },
            eval_version=CURRENT_EVAL_VERSION - 1,
            issue_data_hash="older-stale-hash",
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(
                test_db_session,
                project,
                current_version_issue,
                old_version_issue,
                current_version_evaluation,
                old_version_evaluation,
            )
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            self.client = client
            first = self.client.get(
                "/api/eval/next", headers={"Authorization": f"Bearer {token}"}
            )
            second = self.client.get(
                "/api/eval/next", headers={"Authorization": f"Bearer {token}"}
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["issue_id"] == 2
        assert second.json()["issue_id"] == 1

    def test_old_version_open_before_old_version_closed(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        closed_issue = make_issue(
            id=1,
            project_id=1,
            external_id="1",
            title="Closed old version issue",
            state="closed",
        )
        open_issue = make_issue(
            id=2,
            project_id=1,
            external_id="2",
            title="Open old version issue",
        )
        closed_evaluation = make_evaluation(
            id=1,
            issue_id=1,
            latest=True,
            summary="This summary is intentionally long enough.",
            suggested_action="close",
            suggested_action_reason="This old-version evaluation must be refreshed.",
            scores={},
            eval_version=CURRENT_EVAL_VERSION - 1,
            issue_data_hash="old-closed-hash",
        )
        open_evaluation = make_evaluation(
            id=2,
            issue_id=2,
            latest=True,
            summary="This summary is intentionally long enough.",
            suggested_action="keep_open",
            suggested_action_reason="This old-version evaluation must be refreshed.",
            scores={
                "staleness": 1,
                "complexity": 1,
                "support_request": 1,
                "readiness": 1,
            },
            eval_version=CURRENT_EVAL_VERSION - 1,
            issue_data_hash="old-open-hash",
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(
                test_db_session,
                project,
                closed_issue,
                open_issue,
                closed_evaluation,
                open_evaluation,
            )
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            self.client = client
            first = self.client.get(
                "/api/eval/next?open_only=false",
                headers={"Authorization": f"Bearer {token}"},
            )
            second = self.client.get(
                "/api/eval/next?open_only=false",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["issue_id"] == 2
        assert second.json()["issue_id"] == 1

    def test_current_version_hash_change_open_before_closed(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        closed_issue = make_issue(
            id=1,
            project_id=1,
            external_id="1",
            title="Closed changed issue",
            state="closed",
        )
        open_issue = make_issue(
            id=2,
            project_id=1,
            external_id="2",
            title="Open changed issue",
        )
        closed_evaluation = make_evaluation(
            id=1,
            issue_id=1,
            latest=True,
            summary="This summary is intentionally long enough.",
            suggested_action="close",
            suggested_action_reason="This issue changed after the last evaluation.",
            scores={},
            eval_version=CURRENT_EVAL_VERSION,
            issue_data_hash="stale-closed-hash",
        )
        open_evaluation = make_evaluation(
            id=2,
            issue_id=2,
            latest=True,
            summary="This summary is intentionally long enough.",
            suggested_action="keep_open",
            suggested_action_reason="This issue changed after the last evaluation.",
            scores={
                "staleness": 1,
                "complexity": 1,
                "support_request": 1,
                "readiness": 1,
            },
            eval_version=CURRENT_EVAL_VERSION,
            issue_data_hash="stale-open-hash",
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(
                test_db_session,
                project,
                closed_issue,
                open_issue,
                closed_evaluation,
                open_evaluation,
            )
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            self.client = client
            first = self.client.get(
                "/api/eval/next?open_only=false",
                headers={"Authorization": f"Bearer {token}"},
            )
            second = self.client.get(
                "/api/eval/next?open_only=false",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["issue_id"] == 2
        assert second.json()["issue_id"] == 1

    def test_same_priority_uses_issue_id_as_tiebreaker(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        higher_id_issue = make_issue(
            id=2,
            project_id=1,
            external_id="2",
            title="Higher id issue",
        )
        lower_id_issue = make_issue(
            id=1,
            project_id=1,
            external_id="1",
            title="Lower id issue",
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, higher_id_issue, lower_id_issue)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            self.client = client
            first = self.client.get(
                "/api/eval/next", headers={"Authorization": f"Bearer {token}"}
            )
            second = self.client.get(
                "/api/eval/next", headers={"Authorization": f"Bearer {token}"}
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["issue_id"] == 1
        assert second.json()["issue_id"] == 2


class TestEvalResultIntegration:
    """Integration tests for POST /api/eval/result."""

    def test_submit_result_rejects_stale_hash(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, title="Regression in pack step")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.post(
                "/api/eval/result",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "issue_id": 1,
                    "content_hash": "stale-hash",
                    "summary": "This summary is definitely long enough.",
                    "scores": {
                        "staleness": 1,
                        "complexity": 3,
                        "support_request": 4,
                        "readiness": 5,
                    },
                    "suggested_action": "keep_open",
                    "suggested_action_reason": "Maintainers still need to investigate this regression.",
                },
            )

        assert response.status_code == 409
        assert "Content hash mismatch" in response.json()["detail"]

    def test_submit_result_validates_payload(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, title="Regression in pack step")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )
        app, token = _create_eval_app(test_db_session)
        current_hash = _compute_content_hash(
            issue.title,
            issue.body,
            issue.state,
            issue.labels,
            issue.comments,
        )

        with TestClient(app) as client:
            response = client.post(
                "/api/eval/result",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "issue_id": 1,
                    "content_hash": current_hash,
                    "summary": "too short",
                    "scores": {
                        "staleness": 1,
                        "complexity": 3,
                        "support_request": 4,
                        "readiness": 5,
                    },
                    "suggested_action": "keep_open",
                    "suggested_action_reason": "Maintainers still need to investigate this regression.",
                },
            )

        assert response.status_code == 422
        assert "Summary must be at least" in response.json()["detail"]

    def test_submit_result_stores_new_latest_evaluation(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, title="Regression in pack step")
        previous = make_evaluation(
            id=1,
            issue_id=1,
            latest=True,
            model_name="pending",
            summary="This summary is intentionally long enough.",
            suggested_action="keep_open",
            suggested_action_reason="The issue still needs maintainer investigation.",
            scores={
                "staleness": 0,
                "complexity": 0,
                "support_request": 0,
                "confidence": 0,
            },
            issue_data_hash=None,
            eval_locked_until=datetime.now(tz=UTC) + timedelta(minutes=10),
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue, previous)
        )
        app, token = _create_eval_app(test_db_session)
        current_hash = _compute_content_hash(
            issue.title,
            issue.body,
            issue.state,
            issue.labels,
            issue.comments,
        )

        with TestClient(app) as client:
            response = client.post(
                "/api/eval/result",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "issue_id": 1,
                    "content_hash": current_hash,
                    "summary": "Maintainers confirmed the regression is still reproducible.",
                    "scores": {
                        "staleness": 2,
                        "complexity": 55,
                        "support_request": 12,
                        "confidence": 70,
                    },
                    "suggested_action": "keep_open",
                    "suggested_action_reason": "The issue is actionable and still affects current builds.",
                    "tokens_used": 120,
                    "prompt_tokens": 80,
                    "completion_tokens": 40,
                    "model_used": "haiku",
                    "llm_backend": "local",
                },
            )

        assert response.status_code == 200
        assert response.json() == {"status": "stored", "issue_id": 1}

        evaluations = asyncio.get_event_loop().run_until_complete(
            _all_evaluations(test_db_session)
        )
        assert len(evaluations) == 2
        assert evaluations[0].latest is False
        assert evaluations[0].eval_locked_until is None
        assert evaluations[1].latest is True
        assert evaluations[1].summary == (
            "Maintainers confirmed the regression is still reproducible."
        )
        assert evaluations[1].model_name == "haiku"
        assert evaluations[1].llm_backend == "local"
        assert evaluations[1].issue_data_hash == current_hash
        assert evaluations[1].eval_version == CURRENT_EVAL_VERSION
        assert evaluations[1].eval_locked_until is not None

    def test_submit_result_stores_current_eval_version(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(
            id=1,
            project_id=1,
            external_id="42",
            title="Regression in pack step",
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )
        app, token = _create_eval_app(test_db_session)
        current_hash = _compute_content_hash(
            issue.title,
            issue.body,
            issue.state,
            issue.labels,
            issue.comments,
        )

        with TestClient(app) as client:
            response = client.post(
                "/api/eval/result",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "issue_id": 1,
                    "content_hash": current_hash,
                    "summary": "Maintainers confirmed the regression is still reproducible.",
                    "scores": {
                        "staleness": 2,
                        "complexity": 55,
                        "support_request": 12,
                        "confidence": 70,
                    },
                    "suggested_action": "keep_open",
                    "suggested_action_reason": "The issue is actionable and still affects current builds.",
                    "tokens_used": 120,
                    "prompt_tokens": 80,
                    "completion_tokens": 40,
                    "model_used": "haiku",
                    "llm_backend": "local",
                },
            )

        assert response.status_code == 200

        evaluations = asyncio.get_event_loop().run_until_complete(
            _all_evaluations(test_db_session)
        )
        assert len(evaluations) == 1
        assert evaluations[0].eval_version == CURRENT_EVAL_VERSION

    def test_submit_result_stores_embedding(
        self, test_db_session: AsyncSession
    ) -> None:
        """Submitted summary_embedding is persisted on the LLMEvaluation row."""
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, title="Regression in pack step")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )
        app, token = _create_eval_app(test_db_session)
        current_hash = _compute_content_hash(
            issue.title,
            issue.body,
            issue.state,
            issue.labels,
            issue.comments,
        )
        embedding = [0.1] * 1024

        with TestClient(app) as client:
            response = client.post(
                "/api/eval/result",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "issue_id": 1,
                    "content_hash": current_hash,
                    "summary": "Maintainers confirmed the regression is still reproducible.",
                    "scores": {
                        "staleness": 2,
                        "complexity": 55,
                        "support_request": 12,
                        "confidence": 70,
                    },
                    "suggested_action": "keep_open",
                    "suggested_action_reason": "The issue is actionable.",
                    "tokens_used": 120,
                    "prompt_tokens": 80,
                    "completion_tokens": 40,
                    "model_used": "haiku",
                    "llm_backend": "local",
                    "summary_embedding": embedding,
                },
            )

        assert response.status_code == 200

        evaluations = asyncio.get_event_loop().run_until_complete(
            _all_evaluations(test_db_session)
        )
        assert len(evaluations) == 1
        assert list(evaluations[0].summary_embedding) == embedding

    """Integration tests for GET /api/eval/status."""

    def test_status_reports_queue_counts(self, test_db_session: AsyncSession) -> None:
        now = datetime.now(tz=UTC)
        project = make_project(id=1, name="snapcraft")
        pending_issue = make_issue(id=1, project_id=1, external_id="1", title="Pending")
        locked_issue = make_issue(id=2, project_id=1, external_id="2", title="Locked")
        evaluated_issue = make_issue(id=3, project_id=1, external_id="3", title="Done")
        expired_pending_issue = make_issue(
            id=4, project_id=1, external_id="4", title="Expired pending"
        )
        closed_issue = make_issue(
            id=5, project_id=1, external_id="5", title="Closed", state="closed"
        )
        locked_eval = make_evaluation(
            id=1,
            issue_id=2,
            latest=True,
            model_name="pending",
            evaluated_at=now,
            eval_locked_until=now + timedelta(minutes=10),
        )
        latest_eval = make_evaluation(
            id=2,
            issue_id=3,
            latest=True,
            evaluated_at=now,
            eval_locked_until=None,
            eval_version=CURRENT_EVAL_VERSION,
        )
        expired_pending_eval = make_evaluation(
            id=3,
            issue_id=4,
            latest=True,
            model_name="pending",
            evaluated_at=now,
            eval_locked_until=now - timedelta(minutes=1),
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(
                test_db_session,
                project,
                pending_issue,
                locked_issue,
                evaluated_issue,
                expired_pending_issue,
                closed_issue,
                locked_eval,
                latest_eval,
                expired_pending_eval,
            )
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/status", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 200
        assert response.json() == {
            "pending": 2,
            "locked": 1,
            "evaluated_today": 1,
            "total_evaluated": 1,
            "total_open": 4,
            "pending_embeddings": 1,
        }


class TestEmbedNextIntegration:
    """Integration tests for GET /api/eval/embed-next."""

    def test_embed_next_requires_auth(self, test_db_session: AsyncSession) -> None:
        app, _token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get("/api/eval/embed-next")

        assert response.status_code == 401

    def test_embed_next_returns_204_when_no_pending_embeddings(
        self, test_db_session: AsyncSession
    ) -> None:
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/embed-next", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 204

    def test_embed_next_returns_issue_with_evaluation_and_no_embedding(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, title="Build fails on arm64")
        evaluation = make_evaluation(
            id=1,
            issue_id=1,
            summary="Arm64 build regression after kernel update.",
            latest=True,
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue, evaluation)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/embed-next", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["issue_id"] == 1
        assert data["project_name"] == "snapcraft"
        assert data["external_id"] == issue.external_id
        assert (
            data["embed_text"]
            == "Build fails on arm64. Arm64 build regression after kernel update."
        )

    def test_embed_next_skips_issue_already_embedded(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, title="Already embedded issue")
        evaluation = make_evaluation(
            id=1,
            issue_id=1,
            summary="This issue already has an embedding.",
            latest=True,
            summary_embedding=[0.1] * 1024,
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue, evaluation)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/embed-next", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 204

    def test_embed_next_skips_pending_evaluation(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, title="Pending issue")
        evaluation = make_evaluation(
            id=1,
            issue_id=1,
            model_name="pending",
            summary=None,
            latest=True,
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue, evaluation)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/embed-next", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 204

    def test_embed_next_locks_returned_issue(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, title="Needs embedding")
        evaluation = make_evaluation(
            id=1,
            issue_id=1,
            summary="A complete summary.",
            latest=True,
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue, evaluation)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/embed-next", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 200

        evaluations = asyncio.get_event_loop().run_until_complete(
            _all_evaluations(test_db_session)
        )
        assert evaluations[0].eval_locked_until is not None
        assert evaluations[0].eval_locked_until.replace(tzinfo=UTC) > datetime.now(
            tz=UTC
        )

        # Second call should return 204 because the issue is now locked
        with TestClient(app) as client:
            second_response = client.get(
                "/api/eval/embed-next", headers={"Authorization": f"Bearer {token}"}
            )
        assert second_response.status_code == 204

    def test_embed_next_skips_empty_summary(
        self, test_db_session: AsyncSession
    ) -> None:
        """Evaluations with an empty summary must not appear in the embed queue."""
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(
            id=1, project_id=1, title="Issue with empty summary", external_id="1"
        )
        evaluation = make_evaluation(id=1, issue_id=1, summary="", latest=True)
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue, evaluation)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/embed-next", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 204

    def test_embed_next_skips_non_latest_evaluation(
        self, test_db_session: AsyncSession
    ) -> None:
        """Only the latest evaluation for an issue should be considered."""
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(
            id=1, project_id=1, title="Issue with old eval", external_id="1"
        )
        old_evaluation = make_evaluation(
            id=1, issue_id=1, summary="Old summary.", latest=False
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue, old_evaluation)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/embed-next", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 204

    def test_embed_next_returns_lowest_issue_id_first(
        self, test_db_session: AsyncSession
    ) -> None:
        """embed-next must return issues ordered by issue_id ascending."""
        project = make_project(id=1, name="snapcraft")
        issue_a = make_issue(id=10, project_id=1, title="Later issue", external_id="10")
        issue_b = make_issue(id=5, project_id=1, title="Earlier issue", external_id="5")
        eval_a = make_evaluation(
            id=1, issue_id=10, summary="Summary for A.", latest=True
        )
        eval_b = make_evaluation(
            id=2, issue_id=5, summary="Summary for B.", latest=True
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue_a, issue_b, eval_a, eval_b)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/embed-next", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 200
        assert response.json()["issue_id"] == 5


class TestEmbedResultIntegration:
    """Integration tests for POST /api/eval/embed-result."""

    def test_embed_result_requires_auth(self, test_db_session: AsyncSession) -> None:
        app, _token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.post(
                "/api/eval/embed-result",
                json={"issue_id": 1, "summary_embedding": [0.1, 0.2]},
            )

        assert response.status_code == 401

    def test_embed_result_stores_embedding(self, test_db_session: AsyncSession) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1)
        evaluation = make_evaluation(
            id=1, issue_id=1, summary="Summary text.", latest=True
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue, evaluation)
        )
        app, token = _create_eval_app(test_db_session)
        embedding = [0.1] * 1024

        with TestClient(app) as client:
            response = client.post(
                "/api/eval/embed-result",
                json={"issue_id": 1, "summary_embedding": embedding},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 200
        assert response.json() == {"status": "stored", "issue_id": 1}

        evaluations = asyncio.get_event_loop().run_until_complete(
            _all_evaluations(test_db_session)
        )
        assert (
            evaluations[0].summary_embedding == embedding
            or list(evaluations[0].summary_embedding) == embedding
        )
        assert evaluations[0].eval_locked_until is None

    def test_embed_result_returns_404_for_unknown_issue(
        self, test_db_session: AsyncSession
    ) -> None:
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.post(
                "/api/eval/embed-result",
                json={"issue_id": 9999, "summary_embedding": [0.1, 0.2]},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 404

    def test_embed_result_returns_422_for_empty_embedding(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1)
        evaluation = make_evaluation(id=1, issue_id=1, latest=True)
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue, evaluation)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.post(
                "/api/eval/embed-result",
                json={"issue_id": 1, "summary_embedding": []},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 422


class TestFilteredIssuesIntegration:
    """Integration tests for filtered_issues config in eval queue and embed queue."""

    def _create_app_with_filter(
        self,
        test_db_session: AsyncSession,
        filtered_issues: dict,
    ) -> tuple[FastAPI, str]:
        app = create_app()
        app.router.lifespan_context = _noop_lifespan
        app.state.config = DashboardConfig(
            maintainers=["alice"], filtered_issues=filtered_issues
        )
        app.state.settings = Settings()
        app.state.settings.eval_api_token = _TEST_EVAL_TOKEN

        async def _override() -> AsyncGenerator[AsyncSession, None]:
            yield test_db_session

        app.dependency_overrides[get_db_session] = _override
        return app, _TEST_EVAL_TOKEN

    def test_eval_next_skips_filtered_issue(
        self, test_db_session: AsyncSession
    ) -> None:
        """Filtered issues must not appear in the eval queue."""
        project = make_project(id=1, name="snapcraft")
        filtered_issue = make_issue(
            id=1, project_id=1, external_id="4472", title="Dependency Dashboard"
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, filtered_issue)
        )
        app, token = self._create_app_with_filter(
            test_db_session, {"snapcraft": ["4472"]}
        )

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/next", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 204

    def test_embed_next_skips_filtered_issue(
        self, test_db_session: AsyncSession
    ) -> None:
        """Filtered issues must not appear in the embed queue."""
        project = make_project(id=1, name="snapcraft")
        filtered_issue = make_issue(
            id=1, project_id=1, external_id="4472", title="Dependency Dashboard"
        )
        evaluation = make_evaluation(
            id=1, issue_id=1, summary="Bot issue.", latest=True
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, filtered_issue, evaluation)
        )
        app, token = self._create_app_with_filter(
            test_db_session, {"snapcraft": ["4472"]}
        )

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/embed-next", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 204

    def test_non_filtered_issue_still_returned(
        self, test_db_session: AsyncSession
    ) -> None:
        """Issues not in filtered_issues must still be returned normally."""
        project = make_project(id=1, name="snapcraft")
        normal_issue = make_issue(
            id=1, project_id=1, external_id="100", title="Real bug"
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, normal_issue)
        )
        app, token = self._create_app_with_filter(
            test_db_session, {"snapcraft": ["4472"]}
        )

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/next", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 200
        assert response.json()["external_id"] == "100"
