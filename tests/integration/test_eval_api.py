"""Integration tests for eval API endpoints with a real DB."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from craft_dashboard.app import create_app
from craft_dashboard.config import DashboardConfig
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.llm.evaluator import _compute_content_hash
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
        assert evaluations[1].eval_locked_until is not None

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
        embedding = [0.1] * 768

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
        }
