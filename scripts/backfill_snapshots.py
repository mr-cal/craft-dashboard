#!/usr/bin/env python3
"""Backfill historical daily snapshots from issue created_at/closed_at dates.

This script computes historical daily snapshots by replaying issue timelines.
For each project, for each day from the earliest issue's created_at to today:
- Count issues where created_at <= date AND (closed_at > date OR closed_at IS NULL AND state = 'open')
- Split by maintainer (author_is_maintainer) for external/internal counts
- Compute median age of open issues on that date
- Count issues closed on that date
- Upsert into the snapshots table
"""

import sys
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from craft_dashboard.models.issue import Issue
from craft_dashboard.models.project import Project
from craft_dashboard.models.snapshot import Snapshot
from craft_dashboard.settings import Settings


def _make_age_buckets() -> dict[str, list[int]]:
    return {
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


def _record_open_item(
    issue: Issue,
    snapshot_date: date,
    age_buckets: dict[str, list[int]],
) -> None:
    created = issue.created_at.date()
    age = (snapshot_date - created).days
    is_internal = issue.author_is_maintainer
    is_bot = issue.author_is_bot
    type_prefix = "issue" if issue.issue_type == "issue" else "pr"

    age_buckets["all"].append(age)
    age_buckets[f"{type_prefix}_all"].append(age)

    if is_internal:
        age_buckets["internal"].append(age)
        age_buckets[f"{type_prefix}_internal"].append(age)
    elif not is_bot:
        age_buckets["external"].append(age)
        age_buckets[f"{type_prefix}_external"].append(age)

    if is_bot:
        age_buckets["bots"].append(age)
        age_buckets[f"{type_prefix}_bots"].append(age)


def _median(values: list[int]) -> int:
    return int(median(values)) if values else 0


def _count_author_groups(items: list[Issue]) -> tuple[int, int, int]:
    external = sum(
        1 for item in items if not item.author_is_maintainer and not item.author_is_bot
    )
    internal = sum(1 for item in items if item.author_is_maintainer)
    bots = sum(1 for item in items if item.author_is_bot)
    return external, internal, bots


def _compute_median_fields(age_buckets: dict[str, list[int]]) -> dict[str, int]:
    return {
        "median_issue_age": _median(age_buckets["issue_all"]),
        "median_pr_age": _median(age_buckets["pr_all"]),
        "nm_median_issue_age": _median(age_buckets["issue_external"]),
        "nm_median_pr_age": _median(age_buckets["pr_external"]),
        "median_issue_age_internal": _median(age_buckets["issue_internal"]),
        "median_pr_age_internal": _median(age_buckets["pr_internal"]),
        "median_issue_age_bots": _median(age_buckets["issue_bots"]),
        "median_pr_age_bots": _median(age_buckets["pr_bots"]),
        "median_age": _median(age_buckets["all"]),
        "nm_median_age": _median(age_buckets["external"]),
        "median_age_internal": _median(age_buckets["internal"]),
        "median_age_bots": _median(age_buckets["bots"]),
    }


def compute_snapshot_for_date(
    issues: list[Issue],
    snapshot_date: date,
) -> dict[str, Any]:
    """Compute snapshot metrics for a given date.

    Args:
        issues: All issues for the project
        snapshot_date: The date to compute metrics for

    Returns:
        Dictionary with all snapshot metrics

    """
    open_issues_on_date: list[Issue] = []
    open_prs_on_date: list[Issue] = []
    closed_issues_on_date: list[Issue] = []
    closed_prs_on_date: list[Issue] = []
    age_buckets = _make_age_buckets()

    for issue in issues:
        if issue.created_at is None:
            continue

        created = issue.created_at.date()
        closed = issue.closed_at.date() if issue.closed_at else None

        if created <= snapshot_date and (closed is None or closed > snapshot_date):
            if issue.issue_type == "issue":
                open_issues_on_date.append(issue)
            elif issue.issue_type == "pull_request":
                open_prs_on_date.append(issue)
            _record_open_item(issue, snapshot_date, age_buckets)

        if closed == snapshot_date:
            if issue.issue_type == "issue":
                closed_issues_on_date.append(issue)
            elif issue.issue_type == "pull_request":
                closed_prs_on_date.append(issue)

    open_issues_external, open_issues_internal, open_issues_bots = _count_author_groups(
        open_issues_on_date
    )
    open_prs_external, open_prs_internal, open_prs_bots = _count_author_groups(
        open_prs_on_date
    )
    closed_issues_external, closed_issues_internal, closed_issues_bots = (
        _count_author_groups(closed_issues_on_date)
    )
    closed_prs_external, closed_prs_internal, closed_prs_bots = _count_author_groups(
        closed_prs_on_date
    )
    open_bugs = sum(
        1
        for issue in open_issues_on_date
        if isinstance(issue.labels, list) and "bug" in issue.labels
    )

    return {
        "open_issues": len(open_issues_on_date),
        "open_prs": len(open_prs_on_date),
        "open_issues_external": open_issues_external,
        "open_issues_internal": open_issues_internal,
        "open_issues_bots": open_issues_bots,
        "open_prs_external": open_prs_external,
        "open_prs_internal": open_prs_internal,
        "open_prs_bots": open_prs_bots,
        "open_bugs": open_bugs,
        **_compute_median_fields(age_buckets),
        "closed_issues": len(closed_issues_on_date),
        "closed_prs": len(closed_prs_on_date),
        "closed_issues_external": closed_issues_external,
        "closed_issues_internal": closed_issues_internal,
        "closed_issues_bots": closed_issues_bots,
        "closed_prs_external": closed_prs_external,
        "closed_prs_internal": closed_prs_internal,
        "closed_prs_bots": closed_prs_bots,
    }


def backfill_project(session: Session, project: Project) -> None:
    """Backfill snapshots for a single project.

    Args:
        session: Database session
        project: Project to backfill

    """
    print(f"Backfilling {project.name}...")

    # Fetch all issues for this project
    result = session.execute(select(Issue).where(Issue.project_id == project.id))
    issues = list(result.scalars())

    if not issues:
        print(f"  No issues found for {project.name}, skipping.")
        return

    # Find earliest and latest dates
    created_dates = [i.created_at.date() for i in issues if i.created_at]
    if not created_dates:
        print(f"  No valid created_at dates for {project.name}, skipping.")
        return

    earliest_date = min(created_dates)
    today = date.today()

    print(
        f"  Date range: {earliest_date} to {today} ({(today - earliest_date).days} days)"
    )

    # Compute snapshots for each day
    snapshots_to_upsert = []
    current_date = earliest_date

    while current_date <= today:
        metrics = compute_snapshot_for_date(issues, current_date)
        snapshots_to_upsert.append(
            {
                "project_id": project.id,
                "snapshot_date": current_date,
                **metrics,
            }
        )
        current_date += timedelta(days=1)

    print(f"  Computed {len(snapshots_to_upsert)} snapshots, upserting...")

    # Bulk upsert
    if snapshots_to_upsert:
        stmt = insert(Snapshot).values(snapshots_to_upsert)
        stmt = stmt.on_conflict_do_update(
            index_elements=["project_id", "snapshot_date"],
            set_={
                "open_issues": stmt.excluded.open_issues,
                "open_prs": stmt.excluded.open_prs,
                "open_issues_external": stmt.excluded.open_issues_external,
                "open_issues_internal": stmt.excluded.open_issues_internal,
                "open_issues_bots": stmt.excluded.open_issues_bots,
                "open_prs_external": stmt.excluded.open_prs_external,
                "open_prs_internal": stmt.excluded.open_prs_internal,
                "open_prs_bots": stmt.excluded.open_prs_bots,
                "open_bugs": stmt.excluded.open_bugs,
                "median_issue_age": stmt.excluded.median_issue_age,
                "median_pr_age": stmt.excluded.median_pr_age,
                "nm_median_issue_age": stmt.excluded.nm_median_issue_age,
                "nm_median_pr_age": stmt.excluded.nm_median_pr_age,
                "median_issue_age_internal": stmt.excluded.median_issue_age_internal,
                "median_pr_age_internal": stmt.excluded.median_pr_age_internal,
                "median_issue_age_bots": stmt.excluded.median_issue_age_bots,
                "median_pr_age_bots": stmt.excluded.median_pr_age_bots,
                "median_age": stmt.excluded.median_age,
                "nm_median_age": stmt.excluded.nm_median_age,
                "median_age_internal": stmt.excluded.median_age_internal,
                "median_age_bots": stmt.excluded.median_age_bots,
                "closed_issues": stmt.excluded.closed_issues,
                "closed_prs": stmt.excluded.closed_prs,
                "closed_issues_external": stmt.excluded.closed_issues_external,
                "closed_issues_internal": stmt.excluded.closed_issues_internal,
                "closed_issues_bots": stmt.excluded.closed_issues_bots,
                "closed_prs_external": stmt.excluded.closed_prs_external,
                "closed_prs_internal": stmt.excluded.closed_prs_internal,
                "closed_prs_bots": stmt.excluded.closed_prs_bots,
            },
        )
        session.execute(stmt)
        session.commit()

    print(f"  ✓ Backfilled {len(snapshots_to_upsert)} snapshots for {project.name}")


def main() -> None:
    """Run the backfill script."""
    # Change to app directory so Settings finds the .env file.
    import os  # noqa: PLC0415

    app_dir = Path(__file__).parent.parent
    os.chdir(app_dir)

    settings = Settings()

    # Convert async URL to sync URL for synchronous SQLAlchemy
    sync_db_url = settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    )

    engine = create_engine(sync_db_url, echo=False)

    with Session(engine) as session:
        # Fetch all projects
        result = session.execute(select(Project).order_by(Project.display_order))
        projects = list(result.scalars())

        print(f"Found {len(projects)} projects to backfill")
        print("")

        for project in projects:
            backfill_project(session, project)
            print("")

        print("✓ Backfill complete!")


if __name__ == "__main__":
    main()
