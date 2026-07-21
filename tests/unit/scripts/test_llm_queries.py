"""Tests for scripts.llm.queries."""

from datetime import UTC, datetime

import pytest
from craft_dashboard.llm.evaluator import CURRENT_EVAL_VERSION
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from scripts.llm.queries import (
    _build_issue_query,
    count_issue_evaluation_targets,
    fetch_issue_evaluation_breakdown,
    fetch_issue_evaluation_targets,
    stream_issue_evaluation_targets,
)
from sqlalchemy.dialects import postgresql as pg_dialect
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"  # type: ignore[attr-defined]


def _compile(query: object) -> str:
    return str(query.compile(dialect=pg_dialect.dialect()))


class TestBuildIssueQuery:
    def test_applies_project_and_open_filters(self) -> None:
        query = _build_issue_query(project_filter="snapcraft", open_only=True)

        compiled = _compile(query)

        assert "JOIN projects ON issues.project_id = projects.id" in compiled
        assert "LEFT OUTER JOIN llm_evaluations" in compiled
        assert "issues.state = %(state_1)s" in compiled
        assert "projects.name = %(name_1)s" in compiled

    def test_applies_issue_range_filters_with_integer_cast(self) -> None:
        query = _build_issue_query(
            issue_filters=[("charmcraft", 100, 200), ("snapcraft", 7, 7)]
        )

        compiled = _compile(query)

        assert "CAST(issues.external_id AS INTEGER) >= %(param_1)s" in compiled
        assert "CAST(issues.external_id AS INTEGER) <= %(param_2)s" in compiled
        assert "projects.name = %(name_1)s" in compiled
        assert "projects.name = %(name_2)s" in compiled
        assert " OR " in compiled

    def test_applies_incomplete_and_stale_filters(self) -> None:
        query = _build_issue_query(incomplete=True, stale_days=30)

        compiled = _compile(query)
        params = query.compile(dialect=pg_dialect.dialect()).params

        assert "llm_evaluations.issue_id IS NULL" in compiled
        assert "llm_evaluations.summary IS NULL" in compiled
        assert "issues.state = %(state_1)s" in compiled
        assert "llm_evaluations.suggested_action = %(suggested_action_1)s" in compiled
        assert "llm_evaluations.scores IS NULL" in compiled
        assert "llm_evaluations.evaluated_at < %(evaluated_at_1)s" in compiled
        assert isinstance(params["evaluated_at_1"], datetime)
        assert params["evaluated_at_1"].tzinfo is UTC


def _issue(*, issue_id: int, state: str) -> Issue:
    now = datetime.now(tz=UTC)
    return Issue(
        id=issue_id,
        project_id=1,
        source="github",
        external_id=str(issue_id),
        issue_type="issue",
        title=f"Issue {issue_id}",
        body="Body",
        state=state,
        author="alice",
        labels=["bug"],
        comments=[],
        metadata_={},
        created_at=now,
        updated_at=now,
        last_fetched_at=now,
    )


class TestFetchIssueEvaluationTargets:
    @pytest.mark.asyncio
    async def test_fetch_targets_priority_ordering(
        self,
        test_db_engine: AsyncEngine,
    ) -> None:
        session_factory = async_sessionmaker(
            test_db_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with session_factory() as session:
            project = Project(name="snapcraft", category="application")
            session.add(project)
            await session.flush()

            issue_closed_no_eval = _issue(issue_id=1, state="closed")
            issue_open_no_eval = _issue(issue_id=2, state="open")
            issue_current_open = _issue(issue_id=3, state="open")
            issue_stale_closed = _issue(issue_id=4, state="closed")
            issue_stale_open = _issue(issue_id=5, state="open")
            issue_current_closed = _issue(issue_id=6, state="closed")
            for issue in (
                issue_closed_no_eval,
                issue_open_no_eval,
                issue_current_open,
                issue_stale_closed,
                issue_stale_open,
                issue_current_closed,
            ):
                issue.project_id = project.id
                session.add(issue)
            await session.flush()

            session.add_all(
                [
                    LLMEvaluation(
                        issue_id=issue_current_open.id,
                        model_name="eval-model",
                        summary="Current open",
                        suggested_action="keep_open",
                        suggested_action_reason="Still active",
                        scores={},
                        issue_data_hash="hash-3",
                        eval_version=CURRENT_EVAL_VERSION,
                        latest=True,
                    ),
                    LLMEvaluation(
                        issue_id=issue_stale_closed.id,
                        model_name="eval-model",
                        summary="Stale closed",
                        suggested_action="close",
                        suggested_action_reason="Old version",
                        scores={},
                        issue_data_hash="hash-4",
                        eval_version=CURRENT_EVAL_VERSION - 1,
                        latest=True,
                    ),
                    LLMEvaluation(
                        issue_id=issue_stale_open.id,
                        model_name="eval-model",
                        summary="Stale open",
                        suggested_action="keep_open",
                        suggested_action_reason="Old version",
                        scores={},
                        issue_data_hash="hash-5",
                        eval_version=CURRENT_EVAL_VERSION - 1,
                        latest=True,
                    ),
                    LLMEvaluation(
                        issue_id=issue_current_closed.id,
                        model_name="eval-model",
                        summary="Current closed",
                        suggested_action="close",
                        suggested_action_reason="Already current",
                        scores={},
                        issue_data_hash="hash-6",
                        eval_version=CURRENT_EVAL_VERSION,
                        latest=True,
                    ),
                ]
            )
            await session.commit()

        targets = await fetch_issue_evaluation_targets(session_factory)

        assert [target.issue.id for target in targets] == [2, 1, 5, 4, 3, 6]
        assert [target.eval_version for target in targets] == [
            None,
            None,
            CURRENT_EVAL_VERSION - 1,
            CURRENT_EVAL_VERSION - 1,
            CURRENT_EVAL_VERSION,
            CURRENT_EVAL_VERSION,
        ]


async def _seed_two_projects_with_issues(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Seed two projects with a handful of open/closed issues, no evals."""
    async with session_factory() as session:
        snapcraft = Project(name="snapcraft", category="application")
        charmcraft = Project(name="charmcraft", category="application")
        session.add_all([snapcraft, charmcraft])
        await session.flush()

        issues = [
            _issue(issue_id=1, state="open"),
            _issue(issue_id=2, state="open"),
            _issue(issue_id=3, state="closed"),
            _issue(issue_id=4, state="open"),
            _issue(issue_id=5, state="closed"),
        ]
        for issue, project in zip(
            issues, [snapcraft, snapcraft, snapcraft, charmcraft, charmcraft]
        ):
            issue.project_id = project.id
            session.add(issue)
        await session.commit()


class TestCountIssueEvaluationTargets:
    @pytest.mark.asyncio
    async def test_count_matches_fetch_length(
        self, test_db_engine: AsyncEngine
    ) -> None:
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False
        )
        await _seed_two_projects_with_issues(session_factory)

        count = await count_issue_evaluation_targets(session_factory)
        targets = await fetch_issue_evaluation_targets(session_factory)

        assert count == len(targets) == 5

    @pytest.mark.asyncio
    async def test_count_respects_filters(self, test_db_engine: AsyncEngine) -> None:
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False
        )
        await _seed_two_projects_with_issues(session_factory)

        count = await count_issue_evaluation_targets(session_factory, open_only=True)

        assert count == 3


class TestStreamIssueEvaluationTargets:
    @pytest.mark.asyncio
    async def test_streams_all_matching_targets(
        self, test_db_engine: AsyncEngine
    ) -> None:
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False
        )
        await _seed_two_projects_with_issues(session_factory)

        streamed = [
            target.issue.id
            async for target in stream_issue_evaluation_targets(session_factory)
        ]
        fetched = [
            target.issue.id
            for target in await fetch_issue_evaluation_targets(session_factory)
        ]

        assert streamed == fetched

    @pytest.mark.asyncio
    async def test_streams_across_multiple_pages(
        self, test_db_engine: AsyncEngine
    ) -> None:
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False
        )
        await _seed_two_projects_with_issues(session_factory)

        # A page size smaller than the total row count forces at least two
        # offset/limit round-trips, exercising the pagination loop itself.
        streamed = [
            target.issue.id
            async for target in stream_issue_evaluation_targets(
                session_factory, page_size=2
            )
        ]

        assert len(streamed) == 5
        assert len(set(streamed)) == 5


class TestFetchIssueEvaluationBreakdown:
    @pytest.mark.asyncio
    async def test_groups_by_project_and_state(
        self, test_db_engine: AsyncEngine
    ) -> None:
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False
        )
        await _seed_two_projects_with_issues(session_factory)

        breakdown = await fetch_issue_evaluation_breakdown(session_factory)

        assert breakdown == {
            ("snapcraft", "open"): 2,
            ("snapcraft", "closed"): 1,
            ("charmcraft", "open"): 1,
            ("charmcraft", "closed"): 1,
        }

    @pytest.mark.asyncio
    async def test_breakdown_respects_project_filter(
        self, test_db_engine: AsyncEngine
    ) -> None:
        session_factory = async_sessionmaker(
            test_db_engine, class_=AsyncSession, expire_on_commit=False
        )
        await _seed_two_projects_with_issues(session_factory)

        breakdown = await fetch_issue_evaluation_breakdown(
            session_factory, project_filter="snapcraft"
        )

        assert breakdown == {
            ("snapcraft", "open"): 2,
            ("snapcraft", "closed"): 1,
        }
