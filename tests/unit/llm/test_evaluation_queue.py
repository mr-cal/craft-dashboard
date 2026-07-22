"""Unit tests for craft_dashboard.llm.evaluation_queue.build_pending_evaluation_query."""

from datetime import UTC, datetime

from craft_dashboard.llm.content_hash import compute_content_hash
from craft_dashboard.llm.evaluation_queue import build_pending_evaluation_query
from craft_dashboard.llm.evaluator import CURRENT_EVAL_VERSION
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

from tests.factories import make_evaluation, make_issue, make_project

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"


async def _seed(session, *entities) -> None:
    session.add_all(entities)
    await session.commit()


class TestBuildPendingEvaluationQuery:
    """Direct (non-HTTP) tests for the pending-evaluation query builder."""

    async def test_unevaluated_issue_is_returned(self, test_db_session) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, external_id="1")
        await _seed(test_db_session, project, issue)

        query = build_pending_evaluation_query().limit(1)
        row = (await test_db_session.execute(query)).first()

        assert row is not None
        assert row[0].id == 1

    async def test_up_to_date_issue_is_excluded_via_content_hash_column(
        self, test_db_session
    ) -> None:
        """The core O(1) fix: matching content_hash excludes the issue with

        no per-row hash recomputation — the query itself does the exclusion.
        """
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, external_id="1")
        current_hash = compute_content_hash(
            issue.title, issue.body, issue.state, issue.labels, issue.comments
        )
        evaluation = make_evaluation(
            id=1,
            issue_id=1,
            latest=True,
            summary="A long enough summary for validation purposes.",
            suggested_action="keep_open",
            suggested_action_reason="Still relevant.",
            scores={
                "staleness": 0,
                "complexity": 0,
                "support_request": 0,
                "readiness": 0,
            },
            eval_version=CURRENT_EVAL_VERSION,
            issue_data_hash=current_hash,
        )
        await _seed(test_db_session, project, issue, evaluation)

        query = build_pending_evaluation_query().limit(1)
        row = (await test_db_session.execute(query)).first()

        assert row is None

    async def test_content_hash_mismatch_requeues_issue(self, test_db_session) -> None:
        """A stale stored hash (content changed since evaluation) is requeued."""
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, external_id="1")
        evaluation = make_evaluation(
            id=1,
            issue_id=1,
            latest=True,
            summary="A long enough summary for validation purposes.",
            suggested_action="keep_open",
            suggested_action_reason="Still relevant.",
            scores={
                "staleness": 0,
                "complexity": 0,
                "support_request": 0,
                "readiness": 0,
            },
            eval_version=CURRENT_EVAL_VERSION,
            issue_data_hash="stale-hash-does-not-match-current-content",
        )
        await _seed(test_db_session, project, issue, evaluation)

        query = build_pending_evaluation_query().limit(1)
        row = (await test_db_session.execute(query)).first()

        assert row is not None
        assert row[0].id == 1

    async def test_force_returns_up_to_date_issue_anyway(self, test_db_session) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, external_id="1")
        current_hash = compute_content_hash(
            issue.title, issue.body, issue.state, issue.labels, issue.comments
        )
        evaluation = make_evaluation(
            id=1,
            issue_id=1,
            latest=True,
            summary="A long enough summary for validation purposes.",
            suggested_action="keep_open",
            suggested_action_reason="Still relevant.",
            scores={
                "staleness": 0,
                "complexity": 0,
                "support_request": 0,
                "readiness": 0,
            },
            eval_version=CURRENT_EVAL_VERSION,
            issue_data_hash=current_hash,
        )
        await _seed(test_db_session, project, issue, evaluation)

        query = build_pending_evaluation_query(force=True).limit(1)
        row = (await test_db_session.execute(query)).first()

        assert row is not None
        assert row[0].id == 1

    async def test_locked_issue_is_excluded_until_lock_expires(
        self, test_db_session
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, external_id="1")
        now = datetime(2025, 1, 1, tzinfo=UTC)
        locked_eval = LLMEvaluation(
            id=1,
            issue_id=1,
            latest=True,
            model_name="pending",
            evaluated_at=now,
            eval_locked_until=now.replace(hour=1),
            scores={},
        )
        await _seed(test_db_session, project, issue, locked_eval)

        query = build_pending_evaluation_query(now=now).limit(1)
        row = (await test_db_session.execute(query)).first()

        assert row is None
