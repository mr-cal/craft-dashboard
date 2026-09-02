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

#: Columns compute_snapshot_for_date actually reads. Selecting only these
#: (instead of full Issue ORM rows) avoids pulling body/comments/metadata_
#: into memory for every issue, which is what caused this script to be
#: OOM-killed on the production VPS when replaying years of history across
#: ~20k issues at once.
_ISSUE_SNAPSHOT_COLUMNS = (
    Issue.external_id,
    Issue.project_id,
    Issue.issue_type,
    Issue.author_is_maintainer,
    Issue.author_is_bot,
    Issue.labels,
    Issue.created_at,
    Issue.closed_at,
)

#: Snapshot fields computed by compute_snapshot_for_date, reused to build
#: the on_conflict_do_update SET clause without repeating each field twice.
_SNAPSHOT_METRIC_FIELDS = (
    "open_issues",
    "open_prs",
    "open_issues_external",
    "open_issues_internal",
    "open_issues_bots",
    "open_prs_external",
    "open_prs_internal",
    "open_prs_bots",
    "open_bugs",
    "median_issue_age",
    "median_pr_age",
    "nm_median_issue_age",
    "nm_median_pr_age",
    "median_issue_age_internal",
    "median_pr_age_internal",
    "median_issue_age_bots",
    "median_pr_age_bots",
    "median_age",
    "nm_median_age",
    "median_age_internal",
    "median_age_bots",
    "closed_issues",
    "closed_prs",
    "closed_issues_external",
    "closed_issues_internal",
    "closed_issues_bots",
    "closed_prs_external",
    "closed_prs_internal",
    "closed_prs_bots",
)

#: Number of daily snapshots to accumulate in memory before upserting and
#: committing. Keeping this small bounds peak memory at the cost of more
#: (smaller) round-trips to the database.
_COMMIT_BATCH_SIZE = 200


def _upsert_snapshot_batch(session: Session, batch: list[dict]) -> None:
    """Upsert one batch of snapshot dicts and commit immediately.

    Args:
        session: Database session.
        batch: List of snapshot field dicts, each including project_id and
            snapshot_date.

    """
    if not batch:
        return
    stmt = insert(Snapshot).values(batch)
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "snapshot_date"],
        set_={
            field: getattr(stmt.excluded, field) for field in _SNAPSHOT_METRIC_FIELDS
        },
    )
    session.execute(stmt)
    session.commit()


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


def backfill_project(
    session: Session,
    project: Project,
    filtered_issue_ids: set[str] | None = None,
) -> None:
    """Backfill snapshots for a single project.

    Args:
        session: Database session
        project: Project to backfill
        filtered_issue_ids: Set of external_id strings to exclude from snapshots

    """
    print(f"Backfilling {project.name}...")

    # Fetch only the columns compute_snapshot_for_date needs (not full Issue
    # rows with body/comments/metadata_) to keep memory usage low.
    q = select(*_ISSUE_SNAPSHOT_COLUMNS).where(Issue.project_id == project.id)
    if filtered_issue_ids:
        q = q.where(~Issue.external_id.in_(filtered_issue_ids))
    issues = list(session.execute(q).all())

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

    # Compute snapshots for each day, committing in small batches to bound
    # peak memory (this trades a bit of speed for a much lower memory
    # ceiling, since this loop replays every historical day).
    batch: list[dict] = []
    total = 0
    current_date = earliest_date

    while current_date <= today:
        metrics = compute_snapshot_for_date(issues, current_date)
        batch.append(
            {
                "project_id": project.id,
                "snapshot_date": current_date,
                **metrics,
            }
        )
        if len(batch) >= _COMMIT_BATCH_SIZE:
            _upsert_snapshot_batch(session, batch)
            total += len(batch)
            batch = []
        current_date += timedelta(days=1)

    if batch:
        _upsert_snapshot_batch(session, batch)
        total += len(batch)

    print(f"  ✓ Backfilled {total} snapshots for {project.name}")


def backfill_cross_project(
    session: Session,
    all_projects: list[Project],
    filtered_issues: dict[str, list[str]] | None = None,
) -> None:
    """Compute cross-project aggregate snapshots with true medians.

    Loads all issues across all projects and computes a true cross-project
    median (rather than averaging per-project medians).
    """
    # Get or create the aggregate project
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = pg_insert(Project).values(
        name="all-projects",
        category="aggregate",
        github_org="canonical",
        display_order=-1,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["name"],
        set_={"category": "aggregate", "display_order": -1},
    )
    session.execute(stmt)
    session.commit()

    agg_project = session.execute(
        select(Project).where(Project.name == "all-projects")
    ).scalar_one()

    print(f"Computing cross-project aggregate (project_id={agg_project.id})...")

    # Load only the columns compute_snapshot_for_date needs across all real
    # projects, instead of full Issue rows with body/comments/metadata_.
    # This is the step that previously OOM-killed the container: pulling
    # ~20k full Issue rows (with JSONB comments/body) into memory at once.
    project_ids = [p.id for p in all_projects if p.name != "all-projects"]
    id_to_name = {p.id: p.name for p in all_projects}
    q = select(*_ISSUE_SNAPSHOT_COLUMNS).where(
        Issue.project_id.in_(project_ids), Issue.created_at.isnot(None)
    )
    rows = session.execute(q).all()
    # Apply per-project filtering
    all_issues = []
    for row in rows:
        project_name = id_to_name.get(row.project_id)
        excluded = (filtered_issues or {}).get(project_name, [])
        if row.external_id in excluded:
            continue
        all_issues.append(row)
    del rows
    print(f"  Loaded {len(all_issues)} issues across {len(project_ids)} projects")

    if not all_issues:
        print("  No issues found, skipping cross-project backfill")
        return

    # Get the full date range from existing per-project snapshots
    date_rows = session.execute(
        select(Snapshot.snapshot_date)
        .where(Snapshot.project_id.in_(project_ids))
        .distinct()
        .order_by(Snapshot.snapshot_date)
    ).scalars()
    all_dates = list(date_rows)
    print(f"  Computing medians for {len(all_dates)} dates...")

    # Compute and commit in small batches to bound peak memory (trading
    # speed for a much lower, flat memory ceiling).
    batch: list[dict] = []
    total = 0
    for i, snapshot_date in enumerate(all_dates):
        metrics = compute_snapshot_for_date(all_issues, snapshot_date)
        metrics["project_id"] = agg_project.id
        metrics["snapshot_date"] = snapshot_date
        batch.append(metrics)

        if len(batch) >= _COMMIT_BATCH_SIZE:
            _upsert_snapshot_batch(session, batch)
            total += len(batch)
            batch = []

        if (i + 1) % 500 == 0:
            print(f"    {i + 1}/{len(all_dates)} dates processed...")

    if batch:
        _upsert_snapshot_batch(session, batch)
        total += len(batch)

    print(f"  ✓ Backfilled {total} cross-project snapshots")


def main() -> None:
    """Run the backfill script."""
    # Change to app directory so Settings finds the .env file.
    import os

    app_dir = Path(__file__).parent.parent
    os.chdir(app_dir)

    settings = Settings()
    from craft_dashboard.config import load_config

    config = load_config(Path(settings.config_file))
    filtered_issues: dict[str, list[str]] = config.filtered_issues

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
        if filtered_issues:
            print(f"Filtering out issues: {filtered_issues}")
        print("")

        for project in projects:
            if project.name == "all-projects":
                continue
            project_filtered = set(filtered_issues.get(project.name, []))
            backfill_project(
                session, project, filtered_issue_ids=project_filtered or None
            )
            print("")

        backfill_cross_project(
            session, projects, filtered_issues=filtered_issues or None
        )
        print("")

        print("✓ Backfill complete!")


if __name__ == "__main__":
    main()
