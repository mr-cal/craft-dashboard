"""Query helpers for selecting issues that need LLM evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from craft_dashboard.llm.evaluator import CURRENT_EVAL_VERSION
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from sqlalchemy import Integer, Select, and_, case, cast, or_, select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True)
class IssueEvaluationTarget:
    """Issue row selected for evaluation."""

    issue: Issue
    project_name: str
    issue_data_hash: str | None
    eval_version: int | None = None


IssueFilter = tuple[str, int, int]


def _build_issue_query(
    *,
    project_filter: str = "",
    open_only: bool = False,
    issue_filters: list[IssueFilter] | None = None,
    incomplete: bool = False,
    stale_days: int = 0,
) -> Select[tuple[Issue, str, str | None, int | None]]:
    """Build the query used to find issues that may need evaluation."""
    old_version = or_(
        LLMEvaluation.eval_version.is_(None),
        LLMEvaluation.eval_version != CURRENT_EVAL_VERSION,
    )
    priority = case(
        (LLMEvaluation.id.is_(None) & (Issue.state == "open"), 1),
        (LLMEvaluation.id.is_(None) & (Issue.state != "open"), 2),
        (old_version & (Issue.state == "open"), 3),
        (old_version & (Issue.state != "open"), 4),
        else_=5,
    )
    query = (
        select(
            Issue,
            Project.name.label("project_name"),
            LLMEvaluation.issue_data_hash,
            LLMEvaluation.eval_version,
        )
        .join(Project, Issue.project_id == Project.id)
        .outerjoin(
            LLMEvaluation,
            (LLMEvaluation.issue_id == Issue.id) & LLMEvaluation.latest.is_(True),
        )
        .order_by(priority, Issue.id)
    )

    if open_only:
        query = query.where(Issue.state == "open")

    if project_filter:
        query = query.where(Project.name == project_filter)

    if issue_filters:
        conditions = []
        for project_name, min_id, max_id in issue_filters:
            external_id = cast(Issue.external_id, Integer)
            conditions.append(
                and_(
                    Project.name == project_name,
                    external_id >= min_id,
                    external_id <= max_id,
                )
            )
        query = query.where(or_(*conditions))

    if incomplete:
        query = query.where(
            or_(
                LLMEvaluation.issue_id.is_(None),
                LLMEvaluation.summary.is_(None),
                LLMEvaluation.summary == "",
                and_(
                    Issue.state == "open",
                    or_(
                        LLMEvaluation.suggested_action.is_(None),
                        LLMEvaluation.suggested_action == "",
                        LLMEvaluation.scores.is_(None),
                    ),
                ),
            )
        )

    if stale_days > 0:
        cutoff = datetime.now(tz=UTC) - timedelta(days=stale_days)
        query = query.where(
            or_(
                LLMEvaluation.issue_id.is_(None),
                LLMEvaluation.evaluated_at < cutoff,
            )
        )

    return query


async def fetch_issue_evaluation_targets(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    project_filter: str = "",
    open_only: bool = False,
    issue_filters: list[IssueFilter] | None = None,
    incomplete: bool = False,
    stale_days: int = 0,
) -> list[IssueEvaluationTarget]:
    """Load issue rows for evaluation from the database."""
    query = _build_issue_query(
        project_filter=project_filter,
        open_only=open_only,
        issue_filters=issue_filters,
        incomplete=incomplete,
        stale_days=stale_days,
    )

    async with session_factory() as session:
        result = await session.execute(query)
        return [
            IssueEvaluationTarget(
                issue=row[0],
                project_name=row.project_name,
                issue_data_hash=row.issue_data_hash,
                eval_version=row.eval_version,
            )
            for row in result.all()
        ]
