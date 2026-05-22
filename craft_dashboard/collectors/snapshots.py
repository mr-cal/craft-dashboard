"""Snapshot generator for daily issue/PR count tracking."""

import logging
import statistics
from datetime import UTC, date, datetime

logger = logging.getLogger(__name__)


def compute_snapshot_counts(
    issues: list[dict],
    maintainers: set[str],
    today: date | None = None,
) -> dict[str, int]:
    """Compute snapshot counts from a list of issue dicts.

    Args:
        issues: List of dicts with keys: issue_type, state, author, labels,
                created_at, closed_at.
        maintainers: Set of maintainer usernames.
        today: Reference date for age calculations (defaults to today).

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
        "closed_issues": 0,
        "closed_prs": 0,
        "closed_issues_external": 0,
        "closed_issues_internal": 0,
        "closed_prs_external": 0,
        "closed_prs_internal": 0,
        "closed_issues_bots": 0,
        "closed_prs_bots": 0,
    }

    open_issue_ages: list[int] = []
    open_pr_ages: list[int] = []
    open_issue_ages_internal: list[int] = []
    open_pr_ages_internal: list[int] = []
    open_issue_ages_external: list[int] = []
    open_pr_ages_external: list[int] = []
    open_issue_ages_bots: list[int] = []
    open_pr_ages_bots: list[int] = []

    for issue in issues:
        is_internal = issue.get("author") in maintainers
        is_bot = issue.get("author_is_bot", False)
        created_at = issue.get("created_at")
        closed_at = issue.get("closed_at")

        if issue["state"] in ("open",):
            age_days = 0
            if created_at:
                ca = created_at.replace(tzinfo=UTC) if created_at.tzinfo is None else created_at
                age_days = max(0, (today_dt - ca).days)

            if issue["issue_type"] == "issue":
                counts["open_issues"] += 1
                open_issue_ages.append(age_days)
                if is_internal:
                    counts["open_issues_internal"] += 1
                    open_issue_ages_internal.append(age_days)
                elif not is_bot:
                    counts["open_issues_external"] += 1
                    open_issue_ages_external.append(age_days)
                if is_bot:
                    counts["open_issues_bots"] += 1
                    open_issue_ages_bots.append(age_days)
                if "bug" in issue.get("labels", []):
                    counts["open_bugs"] += 1
            elif issue["issue_type"] == "pull_request":
                counts["open_prs"] += 1
                open_pr_ages.append(age_days)
                if is_internal:
                    counts["open_prs_internal"] += 1
                    open_pr_ages_internal.append(age_days)
                elif not is_bot:
                    counts["open_prs_external"] += 1
                    open_pr_ages_external.append(age_days)
                if is_bot:
                    counts["open_prs_bots"] += 1
                    open_pr_ages_bots.append(age_days)

        elif issue["state"] in ("closed", "merged"):
            # Count issues closed exactly on this snapshot day
            if closed_at:
                if closed_at.date() == today:
                    if issue["issue_type"] == "issue":
                        counts["closed_issues"] += 1
                        if is_internal:
                            counts["closed_issues_internal"] += 1
                        elif not is_bot:
                            counts["closed_issues_external"] += 1
                        if is_bot:
                            counts["closed_issues_bots"] += 1
                    elif issue["issue_type"] == "pull_request":
                        counts["closed_prs"] += 1
                        if is_internal:
                            counts["closed_prs_internal"] += 1
                        elif not is_bot:
                            counts["closed_prs_external"] += 1
                        if is_bot:
                            counts["closed_prs_bots"] += 1

    if open_issue_ages:
        counts["median_issue_age"] = int(statistics.median(open_issue_ages))
    if open_pr_ages:
        counts["median_pr_age"] = int(statistics.median(open_pr_ages))
    if open_issue_ages_internal:
        counts["median_issue_age_internal"] = int(statistics.median(open_issue_ages_internal))
    if open_pr_ages_internal:
        counts["median_pr_age_internal"] = int(statistics.median(open_pr_ages_internal))
    if open_issue_ages_external:
        counts["nm_median_issue_age"] = int(statistics.median(open_issue_ages_external))
    if open_pr_ages_external:
        counts["nm_median_pr_age"] = int(statistics.median(open_pr_ages_external))
    if open_issue_ages_bots:
        counts["median_issue_age_bots"] = int(statistics.median(open_issue_ages_bots))
    if open_pr_ages_bots:
        counts["median_pr_age_bots"] = int(statistics.median(open_pr_ages_bots))

    return counts


async def generate_snapshot(
    project_id: int,
    session,  # noqa: ANN001
    maintainers: set[str],
) -> None:
    """Generate a daily snapshot for a project.

    Queries current open issues/PRs and upserts a snapshot row for today.

    Args:
        project_id: The database ID of the project.
        session: An async SQLAlchemy session.
        maintainers: Set of maintainer usernames.

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

    counts = compute_snapshot_counts(issues, maintainers)
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
