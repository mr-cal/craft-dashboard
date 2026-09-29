"""All-time issue and PR volume with trailing 30-day flow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from craft_dashboard.models.issue import Issue
from craft_dashboard.models.project import Project

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession

    from craft_dashboard.services.dashboard.types import VolumeStats


async def _all_time_counts(
    session: AsyncSession,
    excl: ColumnElement[bool] | None,
) -> dict[tuple[str, str], int]:
    """Count every tracked item grouped by issue type and state."""
    query = (
        select(
            Issue.issue_type,
            Issue.state,
            func.count(Issue.id),
        )
        .join(Project, Issue.project_id == Project.id)
        .where(Project.category != "aggregate")
    )
    if excl is not None:
        query = query.where(excl)
    query = query.group_by(Issue.issue_type, Issue.state)
    rows = (await session.execute(query)).all()
    return {(itype, st): cnt for itype, st, cnt in rows}


async def _created_counts_by_type(
    session: AsyncSession,
    excl: ColumnElement[bool] | None,
    thirty_days_ago: datetime,
) -> dict[str, int]:
    """Count items created in the trailing 30 days per issue type."""
    query = (
        select(
            Issue.issue_type,
            func.count(Issue.id),
        )
        .join(Project, Issue.project_id == Project.id)
        .where(
            Issue.created_at >= thirty_days_ago,
            Project.category != "aggregate",
        )
    )
    if excl is not None:
        query = query.where(excl)
    query = query.group_by(Issue.issue_type)
    return {row[0]: row[1] for row in (await session.execute(query)).all()}


async def compute_volume(
    session: AsyncSession,
    excl: ColumnElement[bool] | None,
    thirty_days_ago: datetime,
    closed_30d: tuple[int, int],
) -> VolumeStats:
    """Compute all-time volume plus 30-day created, closed, and net figures.

    ``closed_30d`` carries the 30-day closed issue and PR counts already
    computed for resolution throughput.
    """
    issues_30d, prs_30d = closed_30d
    counts_map = await _all_time_counts(session, excl)

    open_issues = counts_map.get(("issue", "open"), 0)
    closed_issues = counts_map.get(("issue", "closed"), 0)
    open_prs = counts_map.get(("pull_request", "open"), 0)
    closed_prs = counts_map.get(("pull_request", "closed"), 0) + counts_map.get(
        ("pull_request", "merged"), 0
    )
    total_items = open_issues + closed_issues + open_prs + closed_prs
    total_30d_closed = issues_30d + prs_30d

    created_30d_rows = await _created_counts_by_type(session, excl, thirty_days_ago)
    created_issues_30d = created_30d_rows.get("issue", 0)
    created_prs_30d = created_30d_rows.get("pull_request", 0)
    created_total_30d = created_issues_30d + created_prs_30d

    net_open_issues_30d = created_issues_30d - issues_30d
    net_open_prs_30d = created_prs_30d - prs_30d
    net_open_total_30d = created_total_30d - total_30d_closed

    return {
        "open_issues": open_issues,
        "closed_issues": closed_issues,
        "issues_30d_closed": issues_30d,
        "open_issues_30d": net_open_issues_30d,
        "created_issues_30d": created_issues_30d,
        "net_open_issues_30d": net_open_issues_30d,
        "open_prs": open_prs,
        "closed_prs": closed_prs,
        "prs_30d_closed": prs_30d,
        "open_prs_30d": net_open_prs_30d,
        "created_prs_30d": created_prs_30d,
        "net_open_prs_30d": net_open_prs_30d,
        "total_items": total_items,
        "total_30d_closed": total_30d_closed,
        "open_total_30d": net_open_total_30d,
        "created_total_30d": created_total_30d,
        "net_open_total_30d": net_open_total_30d,
    }
