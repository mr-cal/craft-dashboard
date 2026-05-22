"""Snapshot generator for daily issue/PR count tracking."""

import logging
from datetime import UTC, date, datetime, timedelta

__all__ = ["compute_snapshot_counts", "generate_snapshot"]

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


def _normalize_datetime(value: datetime | None, fallback: datetime) -> datetime:
    """Return a timezone-aware datetime, defaulting missing values to fallback."""
    if value is None:
        return fallback
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _starcraft_get_median_date(dates: list[datetime]) -> datetime:
    """Replicate starcraft-stats median-date behavior exactly."""
    sorted_dates = sorted(dates)
    n = len(sorted_dates)
    if n % 2 == 0:
        reference = datetime(year=2000, month=1, day=1, tzinfo=UTC)
        mid1 = sorted_dates[n // 2 - 1]
        mid2 = sorted_dates[n // 2]
        return reference + sum((d - reference for d in [mid1, mid2]), timedelta()) / 2
    return sorted_dates[n // 2]


def _starcraft_get_median_age(
    dates: list[datetime], reference_date: datetime
) -> int | None:
    """Replicate starcraft-stats median-age behavior exactly."""
    if dates:
        return (reference_date - _starcraft_get_median_date(dates)).days
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

    if type_prefix == "issue" and "bug" in issue.get("labels", []):
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
        if (median_age := _starcraft_get_median_age(dates, today_dt)) is not None:
            counts[field] = median_age

    return counts


async def generate_snapshot(
    project_id: int,
    session,  # noqa: ANN001
    maintainers: set[str],
    bots: set[str] | None = None,
) -> None:
    """Generate a daily snapshot for a project.

    Queries current open issues/PRs and upserts a snapshot row for today.

    Args:
        project_id: The database ID of the project.
        session: An async SQLAlchemy session.
        maintainers: Set of maintainer usernames.
        bots: Optional set of configured bot usernames.

    """
    from sqlalchemy import select  # noqa: PLC0415
    from sqlalchemy.dialects.postgresql import insert  # noqa: PLC0415

    from craft_dashboard.models.issue import Issue  # noqa: PLC0415
    from craft_dashboard.models.snapshot import Snapshot  # noqa: PLC0415

    result = await session.execute(
        select(
            Issue.issue_type,
            Issue.state,
            Issue.author,
            Issue.author_is_bot,
            Issue.labels,
            Issue.created_at,
            Issue.closed_at,
        ).where(Issue.project_id == project_id)
    )

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
