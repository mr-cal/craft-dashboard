"""Query helpers for selecting issues that need LLM evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from craft_dashboard.llm.evaluator import CURRENT_EVAL_VERSION
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from sqlalchemy import Integer, Select, and_, case, cast, func, or_, select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True)
class IssueEvaluationTarget:
    """Issue row selected for evaluation."""

    issue: Issue
    project_name: str
    issue_data_hash: str | None
    eval_version: int | None = None


IssueFilter = tuple[str, int, int]

# Default page size for the paginated streaming fetch; large enough to keep
# round-trips infrequent, small enough to avoid materializing the whole
# 19k+ row result set in memory at once.
_STREAM_PAGE_SIZE = 500


def _join_issue_tables(query: Select) -> Select:
    """Join Project and the latest LLMEvaluation onto an Issue-based query.

    Shared by every query variant (full fetch, count, paginated stream, and
    the dry-run breakdown) so the join logic only lives in one place.
    """
    return query.join(Project, Issue.project_id == Project.id).outerjoin(
        LLMEvaluation,
        (LLMEvaluation.issue_id == Issue.id) & LLMEvaluation.latest.is_(True),
    )


def _apply_issue_filters(
    query: Select,
    *,
    project_filter: str = "",
    open_only: bool = False,
    issue_filters: list[IssueFilter] | None = None,
    incomplete: bool = False,
    stale_days: int = 0,
    include_issue_ids: set[int] | None = None,
    exclude_issue_ids: set[int] | None = None,
) -> Select:
    """Apply the shared WHERE clauses used by every issue-selection query."""
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

    if include_issue_ids is not None:
        query = query.where(Issue.id.in_(include_issue_ids))

    if exclude_issue_ids:
        query = query.where(Issue.id.notin_(exclude_issue_ids))

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


def _build_issue_query(
    *,
    project_filter: str = "",
    open_only: bool = False,
    issue_filters: list[IssueFilter] | None = None,
    incomplete: bool = False,
    stale_days: int = 0,
    include_issue_ids: set[int] | None = None,
    exclude_issue_ids: set[int] | None = None,
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
    query = _join_issue_tables(
        select(
            Issue,
            Project.name.label("project_name"),
            LLMEvaluation.issue_data_hash,
            LLMEvaluation.eval_version,
        )
    ).order_by(priority, Issue.id)

    return _apply_issue_filters(
        query,
        project_filter=project_filter,
        open_only=open_only,
        issue_filters=issue_filters,
        incomplete=incomplete,
        stale_days=stale_days,
        include_issue_ids=include_issue_ids,
        exclude_issue_ids=exclude_issue_ids,
    )


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


async def count_issue_evaluation_targets(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    project_filter: str = "",
    open_only: bool = False,
    issue_filters: list[IssueFilter] | None = None,
    incomplete: bool = False,
    stale_days: int = 0,
    include_issue_ids: set[int] | None = None,
    exclude_issue_ids: set[int] | None = None,
) -> int:
    """Count issue rows matching the evaluation filters without loading them.

    Used for dry-run totals and to size progress bars ahead of streaming the
    actual rows, so callers don't need to materialize the full result set
    just to know how many issues there are. ``include_issue_ids`` /
    ``exclude_issue_ids`` let callers ask "how many of these specific IDs
    still match?" (e.g. to size a resumed run against a checkpoint) without
    a second round-trip.
    """
    base_query = _build_issue_query(
        project_filter=project_filter,
        open_only=open_only,
        issue_filters=issue_filters,
        incomplete=incomplete,
        stale_days=stale_days,
        include_issue_ids=include_issue_ids,
        exclude_issue_ids=exclude_issue_ids,
    )
    # Selecting count() over a subquery of the (unordered, since ORDER BY is
    # irrelevant here) base query keeps the join/filter logic in one place
    # rather than duplicating it.
    count_query = select(func.count()).select_from(base_query.order_by(None).subquery())

    async with session_factory() as session:
        result = await session.execute(count_query)
        return result.scalar_one()


async def stream_issue_evaluation_targets(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    project_filter: str = "",
    open_only: bool = False,
    issue_filters: list[IssueFilter] | None = None,
    incomplete: bool = False,
    stale_days: int = 0,
    exclude_issue_ids: set[int] | None = None,
    page_size: int = _STREAM_PAGE_SIZE,
) -> AsyncIterator[IssueEvaluationTarget]:
    """Yield issue rows for evaluation, fetched a page at a time.

    Unlike :func:`fetch_issue_evaluation_targets`, this never materializes
    the whole result set in memory at once -- only `page_size` rows are held
    at a time, which matters when there are tens of thousands of matching
    issues. Each page opens its own short-lived session so a single slow
    consumer doesn't hold one connection open for the whole run.

    ``exclude_issue_ids`` (e.g. issues already completed per a resumed
    checkpoint) is applied in SQL so callers never see them stream past --
    no separate skip-check is needed downstream.
    """
    base_query = _build_issue_query(
        project_filter=project_filter,
        open_only=open_only,
        issue_filters=issue_filters,
        incomplete=incomplete,
        stale_days=stale_days,
        exclude_issue_ids=exclude_issue_ids,
    )

    offset = 0
    while True:
        page_query = base_query.offset(offset).limit(page_size)
        async with session_factory() as session:
            result = await session.execute(page_query)
            rows = result.all()

        if not rows:
            return

        for row in rows:
            yield IssueEvaluationTarget(
                issue=row[0],
                project_name=row.project_name,
                issue_data_hash=row.issue_data_hash,
                eval_version=row.eval_version,
            )

        if len(rows) < page_size:
            return
        offset += page_size


async def fetch_issue_evaluation_breakdown(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    project_filter: str = "",
    open_only: bool = False,
    issue_filters: list[IssueFilter] | None = None,
    incomplete: bool = False,
    stale_days: int = 0,
    exclude_issue_ids: set[int] | None = None,
) -> dict[tuple[str, str], int]:
    """Count matching issues grouped by (project_name, issue_state).

    Powers the richer dry-run breakdown: rather than a single "N issues
    would be evaluated" total, callers can show a per-project, per-state
    table. ``exclude_issue_ids`` mirrors the same checkpoint-exclusion
    applied to the headline dry-run count, so the breakdown adds up to it.
    """
    query = _apply_issue_filters(
        _join_issue_tables(
            select(
                Project.name.label("project_name"),
                Issue.state,
                func.count().label("issue_count"),
            )
        ),
        project_filter=project_filter,
        open_only=open_only,
        issue_filters=issue_filters,
        incomplete=incomplete,
        stale_days=stale_days,
        exclude_issue_ids=exclude_issue_ids,
    ).group_by(Project.name, Issue.state)

    async with session_factory() as session:
        result = await session.execute(query)
        return {(row.project_name, row.state): row.issue_count for row in result.all()}
