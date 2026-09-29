"""Tests for the evaluation retention garbage collector."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.services.evaluation_gc import (
    clear_superseded_embeddings,
    delete_superseded_evaluations,
)


async def _make_issue(session) -> Issue:
    project = Project(name="rockcraft", category="application", github_org="canonical")
    session.add(project)
    await session.flush()
    issue = Issue(
        project_id=project.id,
        source="github",
        external_id="1",
        issue_type="issue",
        last_fetched_at=datetime.now(tz=UTC),
        title="t",
        state="open",
    )
    session.add(issue)
    await session.flush()
    return issue


def _evaluation(issue_id: int, *, latest: bool, age_days: int, embedding=None):
    return LLMEvaluation(
        issue_id=issue_id,
        model_name="m",
        latest=latest,
        evaluated_at=datetime.now(tz=UTC) - timedelta(days=age_days),
        summary_embedding=embedding,
    )


class TestClearSupersededEmbeddings:
    async def test_clears_only_superseded_rows(self, test_db_session) -> None:
        issue = await _make_issue(test_db_session)
        emb = [0.1] * 1024
        current = _evaluation(issue.id, latest=True, age_days=1, embedding=emb)
        old = _evaluation(issue.id, latest=False, age_days=1, embedding=emb)
        test_db_session.add_all([current, old])
        await test_db_session.commit()

        cleared = await clear_superseded_embeddings(test_db_session)

        assert cleared == 1
        await test_db_session.refresh(current)
        await test_db_session.refresh(old)
        assert current.summary_embedding is not None
        assert old.summary_embedding is None

    async def test_dry_run_reports_without_writing(self, test_db_session) -> None:
        issue = await _make_issue(test_db_session)
        old = _evaluation(issue.id, latest=False, age_days=1, embedding=[0.1] * 1024)
        test_db_session.add(old)
        await test_db_session.commit()

        assert await clear_superseded_embeddings(test_db_session, dry_run=True) == 1
        await test_db_session.refresh(old)
        assert old.summary_embedding is not None

    async def test_rejects_non_positive_batch_size(self, test_db_session) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            await clear_superseded_embeddings(test_db_session, batch_size=0)


class TestDeleteSupersededEvaluations:
    async def test_keeps_latest_however_old(self, test_db_session) -> None:
        issue = await _make_issue(test_db_session)
        current = _evaluation(issue.id, latest=True, age_days=9999)
        stale = _evaluation(issue.id, latest=False, age_days=9999)
        test_db_session.add_all([current, stale])
        await test_db_session.commit()

        deleted = await delete_superseded_evaluations(
            test_db_session, retention_days=90
        )

        assert deleted == 1
        remaining = (
            await test_db_session.scalars(LLMEvaluation.__table__.select())
        ).all()
        assert len(remaining) == 1

    async def test_keeps_superseded_rows_inside_the_window(
        self, test_db_session
    ) -> None:
        issue = await _make_issue(test_db_session)
        test_db_session.add(_evaluation(issue.id, latest=False, age_days=10))
        await test_db_session.commit()

        assert (
            await delete_superseded_evaluations(test_db_session, retention_days=90) == 0
        )

    async def test_batches_larger_than_batch_size(self, test_db_session) -> None:
        issue = await _make_issue(test_db_session)
        test_db_session.add_all(
            [_evaluation(issue.id, latest=False, age_days=200) for _ in range(7)]
        )
        await test_db_session.commit()

        deleted = await delete_superseded_evaluations(
            test_db_session, retention_days=90, batch_size=2
        )

        assert deleted == 7

    async def test_dry_run_reports_without_deleting(self, test_db_session) -> None:
        issue = await _make_issue(test_db_session)
        test_db_session.add(_evaluation(issue.id, latest=False, age_days=200))
        await test_db_session.commit()

        assert (
            await delete_superseded_evaluations(
                test_db_session, retention_days=90, dry_run=True
            )
            == 1
        )
        remaining = (
            await test_db_session.scalars(LLMEvaluation.__table__.select())
        ).all()
        assert len(remaining) == 1

    async def test_rejects_negative_retention(self, test_db_session) -> None:
        with pytest.raises(ValueError, match="retention_days"):
            await delete_superseded_evaluations(test_db_session, retention_days=-1)
