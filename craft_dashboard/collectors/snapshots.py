"""Snapshot generator for daily issue/PR count tracking."""

import logging
from datetime import date

logger = logging.getLogger(__name__)


def compute_snapshot_counts(
    issues: list[dict],
    maintainers: set[str],
) -> dict[str, int]:
    """Compute snapshot counts from a list of issue dicts.

    Args:
        issues: List of dicts with keys: issue_type, state, author, labels.
        maintainers: Set of maintainer usernames.

    Returns:
        Dict with snapshot count fields.

    """
    counts = {
        "open_issues": 0,
        "open_prs": 0,
        "open_issues_external": 0,
        "open_issues_internal": 0,
        "open_prs_external": 0,
        "open_prs_internal": 0,
        "open_bugs": 0,
    }

    for issue in issues:
        if issue["state"] not in ("open",):
            continue

        is_internal = issue.get("author") in maintainers

        if issue["issue_type"] == "issue":
            counts["open_issues"] += 1
            if is_internal:
                counts["open_issues_internal"] += 1
            else:
                counts["open_issues_external"] += 1
            if "bug" in issue.get("labels", []):
                counts["open_bugs"] += 1

        elif issue["issue_type"] == "pull_request":
            counts["open_prs"] += 1
            if is_internal:
                counts["open_prs_internal"] += 1
            else:
                counts["open_prs_external"] += 1

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
            Issue.labels,
        ).where(Issue.project_id == project_id)
    )

    issues = [
        {
            "issue_type": row.issue_type,
            "state": row.state,
            "author": row.author,
            "labels": row.labels if isinstance(row.labels, list) else [],
        }
        for row in result
    ]

    counts = compute_snapshot_counts(issues, maintainers)
    today = date.today()

    stmt = insert(Snapshot).values(
        project_id=project_id,
        snapshot_date=today,
        **counts,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "snapshot_date"],
        set_=counts,
    )
    await session.execute(stmt)
    await session.commit()

    logger.info("Generated snapshot for project_id=%d on %s", project_id, today)
