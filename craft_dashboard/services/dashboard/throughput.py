"""Resolution throughput over the trailing 30 and 365 day windows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from craft_dashboard.models.issue import Issue
from craft_dashboard.models.project import Project

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession

    from craft_dashboard.services.dashboard.types import ResolutionThroughput

MONTHS_PER_YEAR = 12


async def _closed_counts_by_type(
    session: AsyncSession,
    excl: ColumnElement[bool] | None,
    since: datetime,
) -> dict[str, int]:
    """Count closed or merged items per issue type since the given moment."""
    query = (
        select(Issue.issue_type, func.count(Issue.id))
        .join(Project, Issue.project_id == Project.id)
        .where(
            Issue.state.in_(["closed", "merged"]),
            Issue.closed_at >= since,
            Project.category != "aggregate",
        )
    )
    if excl is not None:
        query = query.where(excl)
    query = query.group_by(Issue.issue_type)
    return {row[0]: row[1] for row in (await session.execute(query)).all()}


async def compute_resolution_throughput(
    session: AsyncSession,
    excl: ColumnElement[bool] | None,
    thirty_days_ago: datetime,
    one_year_ago: datetime,
) -> ResolutionThroughput:
    """Compute closure counts for the 30-day and 365-day windows."""
    tp_30_rows = await _closed_counts_by_type(session, excl, thirty_days_ago)
    tp_365_rows = await _closed_counts_by_type(session, excl, one_year_ago)

    issues_30d = tp_30_rows.get("issue", 0)
    prs_30d = tp_30_rows.get("pull_request", 0)
    issues_365d = tp_365_rows.get("issue", 0)
    prs_365d = tp_365_rows.get("pull_request", 0)

    total_30d = issues_30d + prs_30d
    total_365d = issues_365d + prs_365d
    total_monthly_avg = (
        int(round(total_365d / MONTHS_PER_YEAR)) if total_365d > 0 else 0
    )

    return {
        "issues_30d": issues_30d,
        "prs_30d": prs_30d,
        "total_30d": total_30d,
        "issues_365d": issues_365d,
        "prs_365d": prs_365d,
        "total_365d": total_365d,
        "total_monthly_avg": total_monthly_avg,
        "total_monthly_delta": total_30d - total_monthly_avg,
    }
