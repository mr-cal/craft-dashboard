"""Integration tests for eval API endpoints with a real DB."""

from __future__ import annotations

import asyncio
import subprocess
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

from craft_dashboard.app import create_app
from craft_dashboard.config import DashboardConfig
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.llm.evaluator import (
    CURRENT_EVAL_VERSION,
    CURRENT_SUMMARY_VERSION,
    _compute_content_hash,
)
from craft_dashboard.models.commit_scan_evidence_path import CommitScanEvidencePath
from craft_dashboard.models.eval_queue_snapshot import EvalQueueSnapshot
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.repositories.issue_repository import IssueRepository
from craft_dashboard.routes import eval_api
from craft_dashboard.settings import Settings
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

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


async def _fetch_evidence_paths(
    session: AsyncSession, *, issue_id: int
) -> list[tuple[str, str]]:
    result = await session.execute(
        select(CommitScanEvidencePath.project, CommitScanEvidencePath.path)
        .where(CommitScanEvidencePath.issue_id == issue_id)
        .order_by(CommitScanEvidencePath.project, CommitScanEvidencePath.path)
    )
    return list(result.all())


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
            comments=[{"author": "bob", "body": "I can reproduce this."}],
        )
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
            "repo_shas": {},
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

    def test_next_returns_204_when_claim_races_with_another_worker(
        self, test_db_session: AsyncSession
    ) -> None:
        """A concurrent worker committing the same claim first must not 500.

        ``FOR UPDATE SKIP LOCKED`` in ``build_pending_evaluation_query`` makes
        this rare, but doesn't make it impossible: another worker can commit
        its own claim on the same never-before-evaluated issue in the narrow
        window between our SELECT and our INSERT. That should surface as
        "no work available right now" (204), not an internal server error.
        """
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, external_id="1", title="Race me")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )
        app, token = _create_eval_app(test_db_session)

        original_commit = test_db_session.commit

        async def _commit_conflict() -> None:
            raise IntegrityError("insert", {}, Exception("duplicate key"))

        test_db_session.commit = _commit_conflict  # type: ignore[method-assign]

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/next", headers={"Authorization": "Bearer " + token}
            )

        test_db_session.commit = original_commit  # type: ignore[method-assign]

        assert response.status_code == 204

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
        # The PR has since been approved: re-fetched with new pr_details,
        # which also updates the denormalized content_hash (as a collector
        # would do on re-collection).
        issue.metadata_ = {"review_status": "approved", "review_count": 1}
        issue.content_hash = _compute_content_hash(
            issue.title,
            issue.body,
            issue.state,
            issue.labels,
            issue.comments,
            pr_details=issue.metadata_,
        )
        issue.content_hash = _compute_content_hash(
            issue.title,
            issue.body,
            issue.state,
            issue.labels,
            issue.comments,
            pr_details=issue.metadata_,
        )
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
            metadata_={
                "review_status": "changes_requested",
                "review_count": 2,
                "ci_passing": ["lint"],
                "ci_failing": ["unit"],
                "ci_pending": [],
                "unresolved_review_comments": 1,
                "diff_additions": 20,
                "diff_deletions": 4,
                "diff_files_changed": 2,
            },
        )
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


class TestQuotaPauseEndpoint:
    """Integration tests for POST /api/eval/quota-pause."""

    def test_requires_auth(self, test_db_session: AsyncSession) -> None:
        app, _token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.post(
                "/api/eval/quota-pause",
                json={"resume_at": datetime.now(UTC).isoformat()},
            )

        assert response.status_code == 401

    def test_records_pause_and_reason(
        self, test_db_session: AsyncSession, monkeypatch
    ) -> None:
        monkeypatch.setattr(eval_api, "_quota_paused_until", None)
        app, token = _create_eval_app(test_db_session)
        resume_at = datetime.now(UTC) + timedelta(minutes=30)

        with TestClient(app) as client:
            response = client.post(
                "/api/eval/quota-pause",
                json={"resume_at": resume_at.isoformat(), "reason": "quota"},
                headers={"Authorization": "Bearer " + token},
            )

        assert response.status_code == 200
        assert response.json() == {"status": "recorded"}
        assert eval_api.get_quota_pause_until() == resume_at

    def test_defaults_reason_to_quota(
        self, test_db_session: AsyncSession, monkeypatch
    ) -> None:
        monkeypatch.setattr(eval_api, "_quota_paused_until", None)
        app, token = _create_eval_app(test_db_session)
        resume_at = datetime.now(UTC) + timedelta(minutes=30)

        with TestClient(app) as client:
            response = client.post(
                "/api/eval/quota-pause",
                json={"resume_at": resume_at.isoformat()},
                headers={"Authorization": "Bearer " + token},
            )

        assert response.status_code == 200


class TestQueueDepthSnapshot:
    """Integration tests for the queue-depth sampling triggered by GET /next."""

    def test_first_next_call_records_a_snapshot(
        self, test_db_session: AsyncSession, monkeypatch
    ) -> None:
        monkeypatch.setattr(eval_api, "_last_queue_snapshot_at", None)
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, external_id="42")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/next", headers={"Authorization": "Bearer " + token}
            )

        assert response.status_code == 200
        snapshots = asyncio.get_event_loop().run_until_complete(
            test_db_session.execute(select(EvalQueueSnapshot))
        )
        rows = list(snapshots.scalars())
        assert len(rows) == 1
        assert rows[0].total_open == 1

    def test_second_call_within_interval_does_not_duplicate(
        self, test_db_session: AsyncSession, monkeypatch
    ) -> None:
        # Throttled so frequent polling doesn't flood the table with samples.
        monkeypatch.setattr(eval_api, "_last_queue_snapshot_at", datetime.now(UTC))
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, external_id="42")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            client.get("/api/eval/next", headers={"Authorization": "Bearer " + token})

        snapshots = asyncio.get_event_loop().run_until_complete(
            test_db_session.execute(select(EvalQueueSnapshot))
        )
        assert list(snapshots.scalars()) == []

    def test_total_open_does_not_fan_out_across_projects(
        self, test_db_session: AsyncSession, monkeypatch
    ) -> None:
        """Regression test: total_open must equal the real open-issue count.

        Previously `total_open`'s query applied a `Project.name`-referencing
        exclusion filter without ever joining `Project`, causing Postgres to
        implicitly cross-join every project into the count — multiplying the
        true open-issue count by the number of projects. With 3 projects and
        2 real open issues, the buggy query would have produced 6, not 2.
        """
        monkeypatch.setattr(eval_api, "_last_queue_snapshot_at", None)
        projects = [
            make_project(id=1, name="snapcraft"),
            make_project(id=2, name="charmcraft"),
            make_project(id=3, name="rockcraft"),
        ]
        issues = [
            make_issue(id=1, project_id=1, external_id="1", state="open"),
            make_issue(id=2, project_id=2, external_id="2", state="open"),
        ]
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, *projects, *issues)
        )
        app, token = _create_eval_app(test_db_session)
        # A non-empty `filtered_issues` config is what triggers the
        # `Project.name`-referencing exclusion clause in the query — without
        # it, `_build_excluded_issues_condition` returns None and the
        # cartesian-product bug this test guards against never manifests.
        app.state.config = DashboardConfig(
            maintainers=["alice", "bob"],
            filtered_issues={"snapcraft": ["999"]},
        )

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/next", headers={"Authorization": "Bearer " + token}
            )

        assert response.status_code in (200, 204)
        snapshots = asyncio.get_event_loop().run_until_complete(
            test_db_session.execute(select(EvalQueueSnapshot))
        )
        rows = list(snapshots.scalars())
        assert len(rows) == 1
        assert rows[0].total_open == 2


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
                    "summary_embedding": [0.1] * 1024,
                    "search_embedding": [0.1] * 1024,
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
                    "summary_embedding": [0.1] * 1024,
                    "search_embedding": [0.1] * 1024,
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
                    "summary_embedding": [0.1] * 1024,
                    "search_embedding": [0.1] * 1024,
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

    def test_submit_result_stamps_evidence_generation(
        self, test_db_session: AsyncSession
    ) -> None:
        """A written evaluation records the issue's current evidence_generation.

        This is the write-side of the Phase 6 staleness check: the evaluation
        must capture the counter it was generated against, so a later scanner
        bump (evidence_generation += 1) makes exactly this evaluation stale —
        not every evaluation in the table at once.
        """
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(
            id=1,
            project_id=1,
            title="Regression in pack step",
            evidence_generation=7,
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
                headers={"Authorization": "Bearer " + token},
                json={
                    "issue_id": 1,
                    "content_hash": current_hash,
                    "summary": "This summary is definitely long enough to pass.",
                    "scores": {
                        "staleness": 2,
                        "complexity": 55,
                        "support_request": 12,
                        "confidence": 70,
                    },
                    "suggested_action": "keep_open",
                    "suggested_action_reason": "The issue is actionable and still affects current builds.",
                    "model_used": "haiku",
                    "llm_backend": "local",
                    "summary_embedding": [0.1] * 1024,
                    "search_embedding": [0.1] * 1024,
                },
            )

        assert response.status_code == 200

        evaluations = asyncio.get_event_loop().run_until_complete(
            _all_evaluations(test_db_session)
        )
        assert len(evaluations) == 1
        assert evaluations[0].evidence_generation == 7

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
                    "summary_embedding": [0.1] * 1024,
                    "search_embedding": [0.1] * 1024,
                },
            )

        assert response.status_code == 200

        evaluations = asyncio.get_event_loop().run_until_complete(
            _all_evaluations(test_db_session)
        )
        assert len(evaluations) == 1
        assert evaluations[0].eval_version == CURRENT_EVAL_VERSION

    def test_submit_result_for_closed_issue_uses_summary_version(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, external_id="1", state="closed")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.post(
                "/api/eval/result",
                headers={"Authorization": "Bearer " + token},
                json={
                    "issue_id": 1,
                    "content_hash": _compute_content_hash(
                        issue.title,
                        issue.body,
                        issue.state,
                        issue.labels,
                        issue.comments,
                        pr_details=issue.metadata_ or None,
                    ),
                    "summary": "Closed issue summary",
                    "scores": {},
                    "tokens_used": 100,
                    "prompt_tokens": 80,
                    "completion_tokens": 20,
                    "model_used": "test-model",
                    "llm_backend": "openrouter",
                    "summary_embedding": [0.1] * 1024,
                    "search_embedding": [0.1] * 1024,
                },
            )

        assert response.status_code == 200

        evaluation = (
            asyncio.get_event_loop()
            .run_until_complete(
                test_db_session.execute(
                    select(LLMEvaluation).where(LLMEvaluation.issue_id == 1)
                )
            )
            .scalar_one()
        )
        assert evaluation.eval_version == CURRENT_SUMMARY_VERSION

    def test_submit_result_persists_cost_usd(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(
            id=1,
            project_id=1,
            external_id="1",
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
            pr_details=issue.metadata_ or None,
        )

        with TestClient(app) as client:
            response = client.post(
                "/api/eval/result",
                headers={"Authorization": "Bearer " + token},
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
                    "llm_backend": "openrouter",
                    "cost_usd": 0.0042,
                    "summary_embedding": [0.1] * 1024,
                    "search_embedding": [0.1] * 1024,
                },
            )

        assert response.status_code == 200

        evaluations = asyncio.get_event_loop().run_until_complete(
            test_db_session.execute(
                select(LLMEvaluation).where(LLMEvaluation.issue_id == 1)
            )
        )
        evaluation = evaluations.scalar_one()
        assert evaluation.cost_usd == 0.0042

    def test_submit_result_stores_embedding(
        self, test_db_session: AsyncSession
    ) -> None:
        """Submitted summary_embedding is persisted on the LLMEvaluation row.

        Submitted search_embedding is persisted on the Issue row, since it
        describes the issue's content (title+body) rather than this
        particular evaluation.
        """
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
        search_embedding = [0.2] * 1024

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
                    "search_embedding": search_embedding,
                },
            )

        assert response.status_code == 200

        evaluations = asyncio.get_event_loop().run_until_complete(
            _all_evaluations(test_db_session)
        )
        assert len(evaluations) == 1
        assert list(evaluations[0].summary_embedding) == embedding

        refreshed_issue = asyncio.get_event_loop().run_until_complete(
            test_db_session.get(Issue, 1)
        )
        assert list(refreshed_issue.search_embedding) == search_embedding

    def test_submit_result_replaces_evidence_paths_and_normalizes_repo_names(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="rockcraft")
        issue = make_issue(id=1, project_id=1, title="Regression in pack step")
        stale_path = CommitScanEvidencePath(
            issue_id=1,
            project="old-project",
            path="old/path.txt",
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue, stale_path)
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
                headers={"Authorization": "Bearer " + token},
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
                    "summary_embedding": [0.1] * 1024,
                    "search_embedding": [0.2] * 1024,
                    "evidence_paths": [
                        {"repo": "canonical/rockcraft", "path": "README.md"},
                        {"repo": "canonical/rockcraft", "path": "README.md"},
                        {"repo": "rockcraft", "path": "src/parts.py"},
                    ],
                },
            )

        assert response.status_code == 200
        assert asyncio.get_event_loop().run_until_complete(
            _fetch_evidence_paths(test_db_session, issue_id=1)
        ) == [
            ("rockcraft", "README.md"),
            ("rockcraft", "src/parts.py"),
        ]


class TestRelatedIssuesEndpoint:
    """Integration tests for GET /api/eval/related."""

    def _create_app_with_openrouter_key(
        self, test_db_session: AsyncSession
    ) -> tuple[FastAPI, str]:
        app, token = _create_eval_app(test_db_session)
        app.state.settings.openrouter_api_key = "test-openrouter-key"
        return app, token

    def _create_app_with_missing_openrouter_key(
        self, test_db_session: AsyncSession
    ) -> tuple[FastAPI, str]:
        app, token = _create_eval_app(test_db_session)
        app.state.settings.openrouter_api_key = ""
        return app, token

    def test_returns_similar_issues(self, test_db_session: AsyncSession) -> None:
        project = make_project(id=1, name="rockcraft")
        source_issue = make_issue(id=1, project_id=1, external_id="1")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, source_issue)
        )
        app, token = self._create_app_with_openrouter_key(test_db_session)
        canned = [
            {
                "id": 2,
                "project_name": "rockcraft",
                "external_id": "2",
                "title": "Similar crash report",
                "summary": "Similar crash report",
                "url": "https://example/2",
                "state": "open",
                "similarity": 0.91,
            }
        ]

        with (
            patch(
                "craft_dashboard.routes.eval_api.EmbeddingClient.embed",
                new=AsyncMock(return_value=[0.1] * 1024),
            ),
            patch.object(
                IssueRepository,
                "find_related_by_summary_embedding",
                new=AsyncMock(return_value=canned),
            ),
            TestClient(app) as client,
        ):
            response = client.get(
                "/api/eval/related",
                params={"issue_id": 1, "query": "crash in the pull step handler"},
                headers={"Authorization": "Bearer " + token},
            )

        assert response.status_code == 200
        assert response.json()["results"][0]["external_id"] == "2"

    def test_returns_json_error_when_embedding_lookup_fails(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="rockcraft")
        source_issue = make_issue(id=1, project_id=1, external_id="1")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, source_issue)
        )
        app, token = self._create_app_with_openrouter_key(test_db_session)

        with (
            patch(
                "craft_dashboard.routes.eval_api.EmbeddingClient.embed",
                new=AsyncMock(side_effect=RuntimeError("embedding unavailable")),
            ),
            TestClient(app) as client,
        ):
            response = client.get(
                "/api/eval/related",
                params={"issue_id": 1, "query": "crash in the pull step handler"},
                headers={"Authorization": "Bearer " + token},
            )

        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {"detail": "Embedding service unavailable"}

    def test_returns_json_error_when_openrouter_key_is_unset(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="rockcraft")
        source_issue = make_issue(id=1, project_id=1, external_id="1")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, source_issue)
        )
        app, token = self._create_app_with_missing_openrouter_key(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/related",
                params={"issue_id": 1, "query": "crash in the pull step handler"},
                headers={"Authorization": "Bearer " + token},
            )

        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {"detail": "Embedding service unavailable"}

    def test_requires_auth(self, test_db_session: AsyncSession) -> None:
        app, _token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/related", params={"issue_id": 1, "query": "x"}
            )

        assert response.status_code == 401


class TestIssueDetailEndpoint:
    """Integration tests for GET /api/eval/issue."""

    def _create_app_with_filter(
        self,
        test_db_session: AsyncSession,
        filtered_issues: dict[str, list[str]],
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

    def test_qualified_ref_resolves_to_one_issue(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="craft-parts")
        issue = make_issue(
            id=1,
            project_id=1,
            external_id="567",
            title="Pull step crash",
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/issue",
                params={"ref": "canonical/craft-parts#567"},
                headers={"Authorization": "Bearer " + token},
            )

        assert response.status_code == 200
        body = response.json()
        assert len(body["candidates"]) == 1
        assert body["candidates"][0]["title"] == "Pull step crash"
        assert body["candidates"][0]["summary"] is None

    def test_qualified_ref_includes_launchpad_issue(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        launchpad_issue = make_issue(
            id=1,
            project_id=1,
            source="launchpad",
            external_id="4472",
            title="Launchpad-only bug",
        )
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, launchpad_issue)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/issue",
                params={"ref": "snapcraft#4472"},
                headers={"Authorization": "Bearer " + token},
            )

        assert response.status_code == 200
        assert [candidate["title"] for candidate in response.json()["candidates"]] == [
            "Launchpad-only bug"
        ]

    def test_bare_ref_returns_all_matching_projects(
        self, test_db_session: AsyncSession
    ) -> None:
        project_a = make_project(id=1, name="craft-parts")
        project_b = make_project(id=2, name="rockcraft")
        issue_a = make_issue(
            id=1, project_id=1, external_id="42", title="In craft-parts"
        )
        issue_b = make_issue(id=2, project_id=2, external_id="42", title="In rockcraft")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project_a, project_b, issue_a, issue_b)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/issue",
                params={"ref": "#42"},
                headers={"Authorization": "Bearer " + token},
            )

        assert response.status_code == 200
        body = response.json()
        assert len(body["candidates"]) == 2
        titles = {candidate["title"] for candidate in body["candidates"]}
        assert titles == {"In craft-parts", "In rockcraft"}

    def test_unresolvable_ref_returns_empty_candidates(
        self, test_db_session: AsyncSession
    ) -> None:
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/issue",
                params={"ref": "canonical/craft-parts#99999"},
                headers={"Authorization": "Bearer " + token},
            )

        assert response.status_code == 200
        assert response.json()["candidates"] == []

    def test_filtered_issue_is_excluded(self, test_db_session: AsyncSession) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, external_id="4472", title="Filtered")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )
        app, token = self._create_app_with_filter(
            test_db_session, {"snapcraft": ["4472"]}
        )

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/issue",
                params={"ref": "snapcraft#4472"},
                headers={"Authorization": "Bearer " + token},
            )

        assert response.status_code == 200
        assert response.json()["candidates"] == []


class TestReleaseEndpoint:
    """POST /api/eval/release drops a claim without recording an evaluation."""

    def test_release_requires_auth(self, test_db_session: AsyncSession) -> None:
        app, _token = _create_eval_app(test_db_session)
        with TestClient(app) as client:
            response = client.post(
                "/api/eval/release",
                json={"issue_id": 1, "reason": "preflight"},
            )
        assert response.status_code == 401

    def test_release_clears_the_pending_lock_and_marks_preflight_blocked(
        self, test_db_session: AsyncSession
    ) -> None:
        project = make_project(id=1, name="rockcraft")
        issue = make_issue(id=1, project_id=1, external_id="1")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )
        app, token = _create_eval_app(test_db_session)

        with TestClient(app) as client:
            claimed = client.get(
                "/api/eval/next", headers={"Authorization": "Bearer " + token}
            )
            assert claimed.status_code == 200
            issue_id = claimed.json()["issue_id"]

            released = client.post(
                "/api/eval/release",
                json={"issue_id": issue_id, "reason": "preflight"},
                headers={"Authorization": "Bearer " + token},
            )
            assert released.status_code == 200

            status = client.get(
                "/api/eval/status", headers={"Authorization": "Bearer " + token}
            )

        assert status.status_code == 200
        assert status.json()["preflight_blocked"] >= 1


class TestEvalStatusIntegration:
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
            "preflight_blocked": 0,
            "evaluated_today": 1,
            "total_evaluated": 1,
            "total_open": 4,
        }


class TestFilteredIssuesIntegration:
    """Integration tests for filtered_issues config in the eval queue."""

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


class TestNextIssueShaPinning:
    """Tests for repo_shas on GET /api/eval/next."""

    @staticmethod
    def _make_bare_mirror_with_commit(
        mirror_dir: Path, tmp_path: Path, name: str
    ) -> None:
        """Create a bare mirror at mirror_dir/<name>.git with one real commit on main."""
        mirror = mirror_dir / f"{name}.git"
        subprocess.run(["git", "init", "-q", "--bare", str(mirror)], check=True)
        # A bare repo with no commits has no resolvable HEAD; give it one
        # real commit via a throwaway working clone so `git rev-parse HEAD`
        # succeeds against the mirror.
        work = tmp_path / f"work-{name}"
        subprocess.run(["git", "clone", "-q", str(mirror), str(work)], check=True)
        subprocess.run(
            ["git", "-C", str(work), "config", "user.email", "t@t.com"], check=True
        )
        subprocess.run(["git", "-C", str(work), "config", "user.name", "T"], check=True)
        (work / "f.txt").write_text("x")
        subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(work), "commit", "-q", "-m", "init"], check=True
        )
        subprocess.run(
            ["git", "-C", str(work), "push", "-q", "origin", "HEAD:refs/heads/main"],
            check=True,
        )
        # `git init --bare` defaults HEAD to refs/heads/master regardless of
        # what branch was just pushed; point it at the branch we pushed so
        # `git rev-parse --verify HEAD` resolves.
        subprocess.run(
            [
                "git",
                "-c",
                "safe.bareRepository=all",
                "-C",
                str(mirror),
                "symbolic-ref",
                "HEAD",
                "refs/heads/main",
            ],
            check=True,
        )

    def test_response_includes_pinned_shas_for_own_project(
        self, test_db_session: AsyncSession, tmp_path: Path
    ) -> None:
        project = make_project(id=1, name="craft-parts", category="library")
        issue = make_issue(id=1, project_id=1, external_id="1", state="open")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )

        mirror_dir = tmp_path / "mirrors"
        mirror_dir.mkdir()
        self._make_bare_mirror_with_commit(mirror_dir, tmp_path, "craft-parts")

        app, token = _create_eval_app(test_db_session)
        # Inject the mirror dir onto the app's ALREADY-built settings object.
        # A `monkeypatch.setenv("CRAFT_DASHBOARD_MIRROR_DIR", ...)` does NOT
        # work: Pydantic Settings binds env at instantiation and
        # app.state.settings is constructed before the test body runs.
        app.state.settings.mirror_dir = str(mirror_dir)

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/next", headers={"Authorization": "Bearer " + token}
            )

        assert response.status_code == 200
        body = response.json()
        assert "repo_shas" in body
        assert "craft-parts" in body["repo_shas"]
        assert len(body["repo_shas"]["craft-parts"]) == 40

    def test_craft_application_project_pins_shas_for_all_craft_libraries(
        self, test_db_session: AsyncSession, tmp_path: Path
    ) -> None:
        """A craft-application project must also pin every craft-library's HEAD."""
        project = make_project(id=1, name="snapcraft", category="application")
        issue = make_issue(id=1, project_id=1, external_id="1", state="open")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )

        mirror_dir = tmp_path / "mirrors"
        mirror_dir.mkdir()
        for name in ("snapcraft", "craft-parts", "craft-cli"):
            self._make_bare_mirror_with_commit(mirror_dir, tmp_path, name)

        app = create_app()
        app.router.lifespan_context = _noop_lifespan
        app.state.config = DashboardConfig(
            maintainers=["alice", "bob"],
            craft_applications=["snapcraft"],
            craft_libraries=["craft-parts", "craft-cli"],
        )
        app.state.settings = Settings()
        app.state.settings.eval_api_token = _TEST_EVAL_TOKEN
        app.state.settings.mirror_dir = str(mirror_dir)

        async def _override() -> AsyncGenerator[AsyncSession, None]:
            yield test_db_session

        app.dependency_overrides[get_db_session] = _override

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/next",
                headers={"Authorization": "Bearer " + _TEST_EVAL_TOKEN},
            )

        assert response.status_code == 200
        body = response.json()
        assert set(body["repo_shas"]) == {"snapcraft", "craft-parts", "craft-cli"}
        for sha in body["repo_shas"].values():
            assert len(sha) == 40

    def test_craft_consumer_project_pins_shas_for_apps_and_libraries(
        self, test_db_session: AsyncSession, tmp_path: Path
    ) -> None:
        """A craft-consumer project (e.g. snapcraft-rocks, which builds rock
        images packaging multiple craft-applications) must pin its own repo
        plus every craft-application and every craft-library's HEAD.
        """
        eval_api.limiter.reset()
        project = make_project(id=1, name="snapcraft-rocks", category="other")
        issue = make_issue(id=1, project_id=1, external_id="1", state="open")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )

        mirror_dir = tmp_path / "mirrors"
        mirror_dir.mkdir()
        for name in ("snapcraft-rocks", "snapcraft", "rockcraft", "craft-parts"):
            self._make_bare_mirror_with_commit(mirror_dir, tmp_path, name)

        app = create_app()
        app.router.lifespan_context = _noop_lifespan
        app.state.config = DashboardConfig(
            maintainers=["alice", "bob"],
            craft_applications=["snapcraft", "rockcraft"],
            craft_libraries=["craft-parts"],
            craft_consumers=["snapcraft-rocks"],
        )
        app.state.settings = Settings()
        app.state.settings.eval_api_token = _TEST_EVAL_TOKEN
        app.state.settings.mirror_dir = str(mirror_dir)

        async def _override() -> AsyncGenerator[AsyncSession, None]:
            yield test_db_session

        app.dependency_overrides[get_db_session] = _override

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/next",
                headers={"Authorization": "Bearer " + _TEST_EVAL_TOKEN},
            )

        assert response.status_code == 200
        body = response.json()
        assert set(body["repo_shas"]) == {
            "snapcraft-rocks",
            "snapcraft",
            "rockcraft",
            "craft-parts",
        }
        for sha in body["repo_shas"].values():
            assert len(sha) == 40

    def test_missing_mirror_omits_that_project_from_repo_shas(
        self, test_db_session: AsyncSession, tmp_path: Path
    ) -> None:
        project = make_project(id=1, name="craft-parts", category="library")
        issue = make_issue(id=1, project_id=1, external_id="1", state="open")
        asyncio.get_event_loop().run_until_complete(
            _seed_entities(test_db_session, project, issue)
        )
        app, token = _create_eval_app(test_db_session)
        app.state.settings.mirror_dir = str(tmp_path / "empty-mirrors")

        with TestClient(app) as client:
            response = client.get(
                "/api/eval/next", headers={"Authorization": "Bearer " + token}
            )

        assert response.status_code == 200
        assert response.json()["repo_shas"] == {}
