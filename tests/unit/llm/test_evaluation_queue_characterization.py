"""Characterization tests for current evaluation queue state transitions."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

from craft_dashboard.app import create_app
from craft_dashboard.config import DashboardConfig
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.llm.content_hash import compute_content_hash
from craft_dashboard.llm.evaluation_queue import build_pending_evaluation_query
from craft_dashboard.llm.evaluator import (
    CLOSED_ISSUE_EVAL_VERSION,
    OPEN_ISSUE_EVAL_VERSION,
)
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.routes.eval_api import _LOCK_TTL
from craft_dashboard.settings import Settings
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.factories import make_evaluation, make_issue, make_project

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession

_AUTH = {"Authorization": "Bearer test-eval-token"}


async def _seed(session: AsyncSession, *entities: object) -> None:
    session.add_all(list(entities))
    await session.commit()


async def _issue_ids(session: AsyncSession, **query_kwargs: object) -> list[int]:
    rows = (await session.execute(build_pending_evaluation_query(**query_kwargs))).all()
    return [row[0].id for row in rows]


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


def _eval_app(session: AsyncSession) -> FastAPI:
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.state.config = DashboardConfig(maintainers=["alice"])
    app.state.settings = Settings()
    app.state.settings.mirror_dir = "nonexistent-test-mirrors"
    app.state.settings.eval_api_token = "test-eval-token"
    app.state.settings.embedding_api_key = "test-key"

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_db_session] = _override
    return app


def _valid_submission(issue_id: int, content_hash: str) -> dict[str, object]:
    return {
        "issue_id": issue_id,
        "content_hash": content_hash,
        "summary": "Maintainers can reproduce this current issue state clearly.",
        "scores": {
            "impact": 35,
            "complexity": 55,
            "actionability": 60,
            "confidence": 70,
        },
        "suggested_action": "keep_open",
        "suggested_action_reason": "The issue remains actionable for maintainers.",
        "tokens_used": 12,
        "prompt_tokens": 7,
        "completion_tokens": 5,
        "model_used": "test-model",
        "llm_backend": "local",
    }


async def test_claim_expires_when_locked_until_is_exactly_deadline(
    test_db_session: AsyncSession,
) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    project = make_project(id=1)
    issue = make_issue(id=1, project_id=1)
    evaluation = make_evaluation(
        id=1,
        issue_id=1,
        latest=True,
        eval_locked_until=now,
        issue_data_hash="stale-hash",
    )
    await _seed(test_db_session, project, issue, evaluation)

    assert await _issue_ids(test_db_session, now=now) == [1]


async def test_claim_expires_only_strictly_after_lease_deadline(
    test_db_session: AsyncSession,
) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    project = make_project(id=1)
    issue = make_issue(id=1, project_id=1)
    evaluation = make_evaluation(
        id=1,
        issue_id=1,
        latest=True,
        eval_locked_until=now + timedelta(microseconds=1),
        issue_data_hash="stale-hash",
    )
    await _seed(test_db_session, project, issue, evaluation)

    assert await _issue_ids(test_db_session, now=now) == []


async def test_only_latest_evaluation_lock_controls_claimability(
    test_db_session: AsyncSession,
) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    project = make_project(id=1)
    issue = make_issue(id=1, project_id=1)
    old_locked = make_evaluation(
        id=1,
        issue_id=1,
        latest=False,
        eval_locked_until=now + timedelta(hours=1),
        issue_data_hash="old-stale-hash",
    )
    latest_unlocked = make_evaluation(
        id=2,
        issue_id=1,
        latest=True,
        eval_locked_until=None,
        issue_data_hash="latest-stale-hash",
    )
    await _seed(test_db_session, project, issue, old_locked, latest_unlocked)

    assert await _issue_ids(test_db_session, now=now) == [1]


async def test_pending_query_priority_orders_never_evaluated_then_stale_versions(
    test_db_session: AsyncSession,
) -> None:
    project = make_project(id=1)
    open_never = make_issue(id=1, project_id=1, external_id="1", state="open")
    closed_never = make_issue(id=2, project_id=1, external_id="2", state="closed")
    open_old_version = make_issue(id=3, project_id=1, external_id="3", state="open")
    closed_old_version = make_issue(id=4, project_id=1, external_id="4", state="closed")
    content_changed = make_issue(id=5, project_id=1, external_id="5", state="open")
    open_hash = compute_content_hash(
        open_old_version.title,
        open_old_version.body,
        open_old_version.state,
        open_old_version.labels,
        open_old_version.comments,
    )
    closed_hash = compute_content_hash(
        closed_old_version.title,
        closed_old_version.body,
        closed_old_version.state,
        closed_old_version.labels,
        closed_old_version.comments,
    )
    stale_open_eval = make_evaluation(
        id=1,
        issue_id=3,
        latest=True,
        eval_version=OPEN_ISSUE_EVAL_VERSION - 1,
        issue_data_hash=open_hash,
    )
    stale_closed_eval = make_evaluation(
        id=2,
        issue_id=4,
        latest=True,
        eval_type="summary",
        eval_version=CLOSED_ISSUE_EVAL_VERSION - 1,
        issue_data_hash=closed_hash,
        suggested_action=None,
        scores={},
    )
    content_changed_eval = make_evaluation(
        id=3,
        issue_id=5,
        latest=True,
        eval_version=OPEN_ISSUE_EVAL_VERSION,
        issue_data_hash="hash-before-content-changed",
    )
    await _seed(
        test_db_session,
        project,
        open_never,
        closed_never,
        open_old_version,
        closed_old_version,
        content_changed,
        stale_open_eval,
        stale_closed_eval,
        content_changed_eval,
    )

    assert await _issue_ids(test_db_session, open_only=False) == [1, 2, 3, 4, 5]


async def test_pending_query_applies_open_only_after_priority_filtering(
    test_db_session: AsyncSession,
) -> None:
    project = make_project(id=1)
    closed_never = make_issue(id=1, project_id=1, state="closed")
    open_changed = make_issue(id=2, project_id=1, external_id="2", state="open")
    changed_eval = make_evaluation(
        id=1,
        issue_id=2,
        latest=True,
        eval_version=OPEN_ISSUE_EVAL_VERSION,
        issue_data_hash="stale-hash",
    )
    await _seed(test_db_session, project, closed_never, open_changed, changed_eval)

    assert await _issue_ids(test_db_session) == [2]
    assert await _issue_ids(test_db_session, open_only=False) == [1, 2]


async def test_pending_query_has_no_implicit_batch_limit(
    test_db_session: AsyncSession,
) -> None:
    project = make_project(id=1)
    issues = [
        make_issue(id=index, project_id=1, external_id=str(index))
        for index in range(1, 4)
    ]
    await _seed(test_db_session, project, *issues)

    assert await _issue_ids(test_db_session) == [1, 2, 3]


async def test_stale_days_requeues_only_before_not_at_cutoff(
    test_db_session: AsyncSession,
) -> None:
    now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    project = make_project(id=1)
    exactly_cutoff = make_issue(id=1, project_id=1, external_id="1")
    before_cutoff = make_issue(id=2, project_id=1, external_id="2")
    eval_at_cutoff = make_evaluation(
        id=1,
        issue_id=1,
        latest=True,
        eval_version=OPEN_ISSUE_EVAL_VERSION,
        evaluated_at=now - timedelta(days=7),
        issue_data_hash=exactly_cutoff.content_hash,
    )
    eval_before_cutoff = make_evaluation(
        id=2,
        issue_id=2,
        latest=True,
        eval_version=OPEN_ISSUE_EVAL_VERSION,
        evaluated_at=now - timedelta(days=7, microseconds=1),
        issue_data_hash=before_cutoff.content_hash,
    )
    await _seed(
        test_db_session,
        project,
        exactly_cutoff,
        before_cutoff,
        eval_at_cutoff,
        eval_before_cutoff,
    )

    assert await _issue_ids(test_db_session, stale_days=7, now=now) == [2]


async def test_next_claim_creates_pending_latest_row_with_future_lock(
    test_db_session: AsyncSession,
) -> None:
    project = make_project(id=1)
    issue = make_issue(id=1, project_id=1, external_id="42")
    await _seed(test_db_session, project, issue)
    app = _eval_app(test_db_session)
    before = datetime.now(tz=UTC)

    with TestClient(app) as client:
        response = client.get("/api/eval/next", headers=_AUTH)

    assert response.status_code == 200
    evaluation = await test_db_session.scalar(select(LLMEvaluation))
    assert evaluation is not None
    assert evaluation.issue_id == 1
    assert evaluation.model_name == "pending"
    assert evaluation.latest is True
    assert evaluation.eval_locked_until is not None
    locked_until = evaluation.eval_locked_until.replace(tzinfo=UTC)
    assert before + _LOCK_TTL <= locked_until <= datetime.now(tz=UTC) + _LOCK_TTL


async def test_submit_result_flips_previous_latest_before_inserting_new_latest(
    test_db_session: AsyncSession,
) -> None:
    project = make_project(id=1)
    issue = make_issue(id=1, project_id=1)
    pending = make_evaluation(
        id=1,
        issue_id=1,
        latest=True,
        model_name="pending",
        eval_locked_until=datetime.now(tz=UTC) + timedelta(minutes=10),
    )
    await _seed(test_db_session, project, issue, pending)
    app = _eval_app(test_db_session)

    with patch(
        "craft_dashboard.routes.eval_api.EmbeddingClient.embed_batch_with_usage",
        new=AsyncMock(return_value=([[0.1] * 1024, [0.2] * 1024], 3)),
    ):
        with TestClient(app) as client:
            response = client.post(
                "/api/eval/result",
                headers=_AUTH,
                json=_valid_submission(1, issue.content_hash),
            )

    assert response.status_code == 200
    evaluations = (
        await test_db_session.scalars(select(LLMEvaluation).order_by(LLMEvaluation.id))
    ).all()
    assert [(row.id, row.latest, row.eval_locked_until) for row in evaluations] == [
        (1, False, None),
        (2, True, None),
    ]


async def test_release_claim_flips_only_pending_latest_to_non_latest(
    test_db_session: AsyncSession,
) -> None:
    project = make_project(id=1)
    issue = make_issue(id=1, project_id=1)
    pending = make_evaluation(id=1, issue_id=1, latest=True, model_name="pending")
    await _seed(test_db_session, project, issue, pending)
    app = _eval_app(test_db_session)

    with TestClient(app) as client:
        response = client.post(
            "/api/eval/release",
            headers=_AUTH,
            json={"issue_id": 1, "reason": "evaluation_error"},
        )

    assert response.status_code == 200
    evaluations = (
        await test_db_session.scalars(select(LLMEvaluation).order_by(LLMEvaluation.id))
    ).all()
    assert [
        (row.model_name, row.latest, row.eval_locked_until) for row in evaluations
    ] == [
        ("pending", False, None),
        ("released:evaluation_error", False, None),
    ]
