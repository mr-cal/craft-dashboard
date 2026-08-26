"""Unit tests for craft_dashboard.services.transcript_gc."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from craft_dashboard.models.evaluation_transcript import EvaluationTranscript
from craft_dashboard.services.transcript_gc import delete_superseded_transcripts
from sqlalchemy import select

from tests.factories import make_evaluation, make_issue, make_project


async def _seed(session, *entities) -> None:
    session.add_all(entities)
    await session.commit()


class TestDeleteSupersededTranscripts:
    """Tests for the transcript retention garbage collector."""

    async def test_keeps_transcripts_of_latest_evaluations(
        self, test_db_session
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, external_id="1")
        evaluation = make_evaluation(id=1, issue_id=1, latest=True)
        await _seed(test_db_session, project, issue, evaluation)
        old_transcript = EvaluationTranscript(
            llm_evaluation_id=1,
            rounds=[],
            model_name="m",
            rounds_used=0,
            created_at=datetime.now(tz=UTC) - timedelta(days=90),
        )
        await _seed(test_db_session, old_transcript)

        deleted = await delete_superseded_transcripts(
            test_db_session, retention_days=30
        )

        assert deleted == 0
        remaining = (
            (await test_db_session.execute(select(EvaluationTranscript)))
            .scalars()
            .all()
        )
        assert len(remaining) == 1

    async def test_deletes_old_transcripts_of_superseded_evaluations(
        self, test_db_session
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, external_id="1")
        superseded_evaluation = make_evaluation(id=1, issue_id=1, latest=False)
        current_evaluation = make_evaluation(id=2, issue_id=1, latest=True)
        await _seed(
            test_db_session,
            project,
            issue,
            superseded_evaluation,
            current_evaluation,
        )
        old_transcript = EvaluationTranscript(
            llm_evaluation_id=1,
            rounds=[],
            model_name="m",
            rounds_used=0,
            created_at=datetime.now(tz=UTC) - timedelta(days=90),
        )
        await _seed(test_db_session, old_transcript)

        deleted = await delete_superseded_transcripts(
            test_db_session, retention_days=30
        )

        assert deleted == 1
        remaining = (
            (await test_db_session.execute(select(EvaluationTranscript)))
            .scalars()
            .all()
        )
        assert remaining == []

    async def test_keeps_recent_transcripts_of_superseded_evaluations(
        self, test_db_session
    ) -> None:
        """Within the retention window, a superseded transcript still survives."""
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, external_id="1")
        superseded_evaluation = make_evaluation(id=1, issue_id=1, latest=False)
        current_evaluation = make_evaluation(id=2, issue_id=1, latest=True)
        await _seed(
            test_db_session,
            project,
            issue,
            superseded_evaluation,
            current_evaluation,
        )
        recent_transcript = EvaluationTranscript(
            llm_evaluation_id=1,
            rounds=[],
            model_name="m",
            rounds_used=0,
            created_at=datetime.now(tz=UTC) - timedelta(days=5),
        )
        await _seed(test_db_session, recent_transcript)

        deleted = await delete_superseded_transcripts(
            test_db_session, retention_days=30
        )

        assert deleted == 0
