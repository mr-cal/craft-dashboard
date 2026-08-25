"""Snapshot generator for daily issue/PR count tracking."""

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.utils import normalize_datetime as _normalize_datetime

__all__ = ["compute_snapshot_counts", "generate_snapshot", "backfill_missing_snapshots"]

logger = logging.getLogger(__name__)


def _increment_counts(
    counts: dict[str, int],
    issue_type: str,
    *,
    is_internal: bool,
    is_bot: bool,
    prefix: str,
) -> None:
    """Increment open/closed counts based on author role."""
    type_key = "issues" if issue_type == "issue" else "prs"
    counts[f"{prefix}_{type_key}"] += 1
    if is_internal:
        counts[f"{prefix}_{type_key}_internal"] += 1
    elif not is_bot:
        counts[f"{prefix}_{type_key}_external"] += 1
    if is_bot:
        counts[f"{prefix}_{type_key}_bots"] += 1


def _get_median_date(dates: list[datetime]) -> datetime:
    """Return the median datetime from a list."""
    sorted_dates = sorted(dates)
    n = len(sorted_dates)
    if n % 2 == 0:
        reference = datetime(year=2000, month=1, day=1, tzinfo=UTC)
        mid1 = sorted_dates[n // 2 - 1]
        mid2 = sorted_dates[n // 2]
        return reference + sum((d - reference for d in [mid1, mid2]), timedelta()) / 2
    return sorted_dates[n // 2]


def _get_median_age(dates: list[datetime], reference_date: datetime) -> int | None:
    """Return the median age in days, or None if the list is empty."""
    if dates:
        return (reference_date - _get_median_date(dates)).days
    return None


def _record_open_item(
    issue: dict,
    created_dt: datetime,
    counts: dict[str, int],
    date_buckets: dict[str, list[datetime]],
    *,
    is_internal: bool,
    is_bot: bool,
) -> None:
    """Record open-item dates for overall, per-type, and per-group medians."""
    type_prefix = "issue" if issue["issue_type"] == "issue" else "pr"

    date_buckets["all"].append(created_dt)
    date_buckets[f"{type_prefix}_all"].append(created_dt)

    if is_internal:
        date_buckets["internal"].append(created_dt)
        date_buckets[f"{type_prefix}_internal"].append(created_dt)
    elif not is_bot:
        date_buckets["external"].append(created_dt)
        date_buckets[f"{type_prefix}_external"].append(created_dt)

    if is_bot:
        date_buckets["bots"].append(created_dt)
        date_buckets[f"{type_prefix}_bots"].append(created_dt)

    if type_prefix == "issue" and "bug" in (issue.get("labels") or []):
        counts["open_bugs"] += 1


def compute_snapshot_counts(
    issues: list[dict],
    maintainers: set[str],
    today: date | None = None,
    bots: set[str] | None = None,
) -> dict[str, int]:
    """Compute snapshot counts from a list of issue dicts.

    Args:
        issues: List of dicts with keys: issue_type, state, author, labels,
                created_at, closed_at.
        maintainers: Set of maintainer usernames.
        today: Reference date for age calculations (defaults to today).
        bots: Optional set of configured bot usernames.

    Returns:
        Dict with snapshot count fields.

    """
    if today is None:
        today = date.today()
    today_dt = datetime.combine(today, datetime.min.time(), tzinfo=UTC)

    counts: dict[str, int] = {
        "open_issues": 0,
        "open_prs": 0,
        "open_issues_external": 0,
        "open_issues_internal": 0,
        "open_prs_external": 0,
        "open_prs_internal": 0,
        "open_issues_bots": 0,
        "open_prs_bots": 0,
        "open_bugs": 0,
        "median_issue_age": 0,
        "median_pr_age": 0,
        "nm_median_issue_age": 0,
        "nm_median_pr_age": 0,
        "median_issue_age_internal": 0,
        "median_pr_age_internal": 0,
        "median_issue_age_bots": 0,
        "median_pr_age_bots": 0,
        "median_age": 0,
        "nm_median_age": 0,
        "median_age_internal": 0,
        "median_age_bots": 0,
        "closed_issues": 0,
        "closed_prs": 0,
        "closed_issues_external": 0,
        "closed_issues_internal": 0,
        "closed_prs_external": 0,
        "closed_prs_internal": 0,
        "closed_issues_bots": 0,
        "closed_prs_bots": 0,
    }

    date_buckets: dict[str, list[datetime]] = {
        "all": [],
        "issue_all": [],
        "pr_all": [],
        "internal": [],
        "issue_internal": [],
        "pr_internal": [],
        "external": [],
        "issue_external": [],
        "pr_external": [],
        "bots": [],
        "issue_bots": [],
        "pr_bots": [],
    }

    for issue in issues:
        is_internal = issue.get("author") in maintainers
        is_bot = issue.get("author_is_bot", False) or (
            bots is not None and issue.get("author", "") in bots
        )
        created_at = issue.get("created_at")
        closed_at = issue.get("closed_at")

        if issue["state"] in ("open",):
            created_dt = _normalize_datetime(created_at, today_dt)

            _increment_counts(
                counts,
                issue_type=issue["issue_type"],
                is_internal=is_internal,
                is_bot=is_bot,
                prefix="open",
            )
            _record_open_item(
                issue,
                created_dt,
                counts,
                date_buckets,
                is_internal=is_internal,
                is_bot=is_bot,
            )

        elif issue["state"] in ("closed", "merged"):
            # Count issues closed exactly on this snapshot day
            if _normalize_datetime(closed_at, today_dt).date() == today:
                _increment_counts(
                    counts,
                    issue_type=issue["issue_type"],
                    is_internal=is_internal,
                    is_bot=is_bot,
                    prefix="closed",
                )

    median_sources = {
        "median_issue_age": date_buckets["issue_all"],
        "median_pr_age": date_buckets["pr_all"],
        "median_age": date_buckets["all"],
        "median_issue_age_internal": date_buckets["issue_internal"],
        "median_pr_age_internal": date_buckets["pr_internal"],
        "median_age_internal": date_buckets["internal"],
        "nm_median_issue_age": date_buckets["issue_external"],
        "nm_median_pr_age": date_buckets["pr_external"],
        "nm_median_age": date_buckets["external"],
        "median_issue_age_bots": date_buckets["issue_bots"],
        "median_pr_age_bots": date_buckets["pr_bots"],
        "median_age_bots": date_buckets["bots"],
    }
    for field, dates in median_sources.items():
        if (median_age := _get_median_age(dates, today_dt)) is not None:
            counts[field] = median_age

    return counts


def _filter_issues_for_date(
    issues: list[dict],
    snapshot_date: date,
) -> list[dict]:
    """Return issues as they existed on *snapshot_date*.

    Uses ``created_at`` and ``closed_at`` to determine each issue's state
    on the given date rather than the current DB state.

    Issues not yet created are excluded. Issues open on the date are returned
    with ``state="open"``. Issues closed on that exact date are returned with
    their original state. Issues already closed before the date are excluded.
    Issues with no ``closed_at`` timestamp are treated as open.

    Args:
        issues: List of issue dicts (as produced by ``generate_snapshot``).
        snapshot_date: The historical date to compute the snapshot for.

    Returns:
        Filtered and state-adjusted list of issue dicts.

    """
    result: list[dict] = []
    for issue in issues:
        created_at = issue.get("created_at")
        closed_at = issue.get("closed_at")

        if created_at is None:
            continue

        created = created_at.date() if hasattr(created_at, "date") else created_at
        closed = (
            closed_at.date() if (closed_at and hasattr(closed_at, "date")) else None
        )

        if created > snapshot_date:
            continue  # not created yet on this date

        if closed is None or closed > snapshot_date:
            # Open on this date: override state so compute_snapshot_counts
            # counts it correctly regardless of the current DB state.
            result.append({**issue, "state": "open"})
        elif closed == snapshot_date:
            # Closed on exactly this date: preserve original state ("closed"/"merged")
            result.append(issue)
        # else: already closed before snapshot_date, exclude

    return result


async def backfill_missing_snapshots(
    project_id: int,
    issues: list[dict],
    session: AsyncSession,
    maintainers: set[str],
    bots: set[str] | None = None,
) -> int:
    """Generate snapshots for any dates missing between the last snapshot and today.

    Computes each missing date's counts from ``created_at`` / ``closed_at``
    timestamps rather than the current DB state, so history stays accurate
    after an outage or delayed collection run.

    Args:
        project_id: The database ID of the project.
        issues: All issue dicts for the project (already loaded from DB).
        session: An async SQLAlchemy session.
        maintainers: Set of maintainer usernames.
        bots: Optional set of configured bot usernames.

    Returns:
        The number of missing snapshots that were backfilled.

    """
    from sqlalchemy import func, select
    from sqlalchemy.dialects.postgresql import insert

    from craft_dashboard.models.snapshot import Snapshot

    today_val = date.today()

    # Find the most recent snapshot date before today
    last_snapshot = await session.scalar(
        select(func.max(Snapshot.snapshot_date)).where(
            Snapshot.project_id == project_id,
            Snapshot.snapshot_date < today_val,
        )
    )

    if last_snapshot is None:
        return 0  # Nothing to backfill from

    filled = 0
    current = last_snapshot + timedelta(days=1)
    while current < today_val:
        # Only generate if this date has no snapshot yet
        exists = await session.scalar(
            select(func.count()).where(
                Snapshot.project_id == project_id,
                Snapshot.snapshot_date == current,
            )
        )
        if not exists:
            historical_issues = _filter_issues_for_date(issues, current)
            counts = compute_snapshot_counts(
                historical_issues, maintainers, today=current, bots=bots
            )
            stmt = insert(Snapshot).values(
                project_id=project_id,
                snapshot_date=current,
                **counts,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["project_id", "snapshot_date"],
                set_=counts,
            )
            await session.execute(stmt)
            filled += 1

        current += timedelta(days=1)

    if filled:
        await session.commit()
        logger.info(
            "Backfilled %d missing snapshots for project_id=%d (up to %s)",
            filled,
            project_id,
            today_val,
        )
    return filled


async def generate_snapshot(
    project_id: int,
    session: AsyncSession,
    maintainers: set[str],
    bots: set[str] | None = None,
    filtered_issue_ids: set[str] | None = None,
) -> None:
    """Generate a daily snapshot for a project.

    Queries all issues from the DB, upserts a snapshot row for today, then
    backfills any dates that were missed since the last snapshot.

    Args:
        project_id: The database ID of the project.
        session: An async SQLAlchemy session.
        maintainers: Set of maintainer usernames.
        bots: Optional set of configured bot usernames.
        filtered_issue_ids: Optional set of external issue IDs to exclude.

    """
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import (
        insert,
    )

    from craft_dashboard.models.issue import (
        Issue,
    )
    from craft_dashboard.models.snapshot import (
        Snapshot,
    )

    issues_q = select(
        Issue.issue_type,
        Issue.state,
        Issue.author,
        Issue.author_is_bot,
        Issue.labels,
        Issue.created_at,
        Issue.closed_at,
    ).where(Issue.project_id == project_id)
    if filtered_issue_ids:
        issues_q = issues_q.where(~Issue.external_id.in_(filtered_issue_ids))
    result = await session.execute(issues_q)

    issues = [
        {
            "issue_type": row.issue_type,
            "state": row.state,
            "author": row.author,
            "author_is_bot": row.author_is_bot,
            "labels": row.labels if isinstance(row.labels, list) else [],
            "created_at": row.created_at,
            "closed_at": row.closed_at,
        }
        for row in result
    ]

    counts = compute_snapshot_counts(issues, maintainers, bots=bots)
    today_val = date.today()

    stmt = insert(Snapshot).values(
        project_id=project_id,
        snapshot_date=today_val,
        **counts,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "snapshot_date"],
        set_=counts,
    )
    await session.execute(stmt)
    await session.commit()

    logger.info("Generated snapshot for project_id=%d on %s", project_id, today_val)

    # Backfill any dates the collector skipped (e.g. after an outage)
    await backfill_missing_snapshots(
        project_id, issues, session, maintainers, bots=bots
    )


async def generate_cross_project_snapshot(
    session: AsyncSession,
    maintainers: set[str],
    bots: set[str] | None = None,
    filtered_issues: dict[str, list[str]] | None = None,
) -> None:
    """Generate a cross-project aggregate snapshot with true medians.

    Queries all open issues across all real projects (not aggregate) and
    computes the true cross-project median ages for today.
    """
    from sqlalchemy import and_
    from sqlalchemy import select as sa_select
    from sqlalchemy.dialects.postgresql import insert

    from craft_dashboard.models.issue import Issue
    from craft_dashboard.models.project import Project
    from craft_dashboard.models.snapshot import Snapshot

    # Get or create the "all-projects" aggregate project
    agg_stmt = insert(Project).values(
        name="all-projects",
        category="aggregate",
        github_org="canonical",
        display_order=-1,
    )
    agg_stmt = agg_stmt.on_conflict_do_update(
        index_elements=["name"],
        set_={"category": "aggregate", "display_order": -1},
    )
    await session.execute(agg_stmt)
    await session.commit()

    agg_result = await session.execute(
        sa_select(Project.id).where(Project.name == "all-projects")
    )
    agg_project_id = agg_result.scalar_one()

    # Get all real project IDs (and their names for per-project filtering)
    proj_result = await session.execute(
        sa_select(Project.id, Project.name).where(Project.category != "aggregate")
    )
    proj_rows = list(proj_result)
    project_ids = [row.id for row in proj_rows]

    # Query all issues across all real projects, excluding filtered issues
    issues_q = sa_select(
        Issue.issue_type,
        Issue.state,
        Issue.author,
        Issue.author_is_bot,
        Issue.labels,
        Issue.created_at,
        Issue.closed_at,
    ).where(Issue.project_id.in_(project_ids))
    if filtered_issues:
        exclusion_conditions = [
            and_(Issue.project_id == row.id, Issue.external_id.in_(ids))
            for row in proj_rows
            if (ids := filtered_issues.get(row.name))
        ]
        if exclusion_conditions:
            from sqlalchemy import or_ as sa_or

            combined = (
                sa_or(*exclusion_conditions)
                if len(exclusion_conditions) > 1
                else exclusion_conditions[0]
            )
            issues_q = issues_q.where(~combined)
    result = await session.execute(issues_q)

    issues = [
        {
            "issue_type": row.issue_type,
            "state": row.state,
            "author": row.author,
            "author_is_bot": row.author_is_bot,
            "labels": row.labels if isinstance(row.labels, list) else [],
            "created_at": row.created_at,
            "closed_at": row.closed_at,
        }
        for row in result
    ]

    counts = compute_snapshot_counts(issues, maintainers, bots=bots)
    today_val = date.today()

    stmt = insert(Snapshot).values(
        project_id=agg_project_id,
        snapshot_date=today_val,
        **counts,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "snapshot_date"],
        set_=counts,
    )
    await session.execute(stmt)
    await session.commit()

    logger.info(
        "Generated cross-project snapshot for %s (project_id=%d)",
        today_val,
        agg_project_id,
    )
