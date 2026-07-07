#!/usr/bin/env python3
r"""Backfill closing_references for existing closed issues.

Queries the database for closed GitHub issues that do not yet have a
closing_references entry in their metadata, then fetches the GitHub timeline
for each one and upserts the result.

This is a one-time (or periodic) backfill for the new closing_references feature;
regular collection will populate the field going forward.

Usage (production):
    podman exec -i vps-infra_craft-dashboard_1 \
        /app/.venv/bin/python /app/scripts/backfill_closing_references.py

Usage (local):
    uv run scripts/backfill_closing_references.py
    uv run scripts/backfill_closing_references.py --project charmcraft
    uv run scripts/backfill_closing_references.py --dry-run
"""

import logging
import sys
import time
from pathlib import Path

import click
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent.parent))

from craft_dashboard.collectors.github import _fetch_closing_references
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.project import Project
from craft_dashboard.settings import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_BATCH_LOG_INTERVAL = 50


@click.command()
@click.option(
    "--project",
    "projects",
    multiple=True,
    help="Only backfill these projects (repeatable). Default: all.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Fetch closing references but do not write to the database.",
)
@click.option(
    "--all",
    "force_all",
    is_flag=True,
    help=(
        "Re-fetch closing references for ALL closed issues, not just those "
        "missing the field. Use to pick up newly-added closing PRs on old issues."
    ),
)
def main(projects: tuple[str, ...], dry_run: bool, force_all: bool) -> None:
    """Backfill closing_references for closed GitHub issues."""
    settings = Settings()
    # Convert async URL to sync URL for synchronous SQLAlchemy
    sync_db_url = settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    engine = create_engine(sync_db_url)

    import github as pygithub

    gh = pygithub.Github(auth=pygithub.Auth.Token(settings.github_token))

    if dry_run:
        logger.info("Dry-run mode — no database writes.")
    if force_all:
        logger.info(
            "Force-all mode — re-fetching closing references for every closed issue."
        )

    with Session(engine) as session:
        project_filter = list(projects) if projects else None

        project_rows = session.execute(select(Project)).scalars().all()
        if project_filter:
            project_rows = [p for p in project_rows if p.name in project_filter]

        for project in project_rows:
            _backfill_project(
                session=session,
                gh=gh,
                project=project,
                dry_run=dry_run,
                force_all=force_all,
            )


def _backfill_project(
    *,
    session: Session,
    gh: object,
    project: Project,
    dry_run: bool,
    force_all: bool,
) -> None:
    """Backfill closing_references for one project."""
    import github as pygithub

    # Find closed GitHub issues missing closing_references (or all if force_all).
    base_where = [
        Issue.project_id == project.id,
        Issue.source == "github",
        Issue.state == "closed",
        Issue.issue_type == "issue",  # PRs don't use closing_references
    ]
    if not force_all:
        base_where.append(Issue.metadata_["closing_references"].is_(None))

    issues = session.execute(
        select(Issue.id, Issue.external_id).where(*base_where)
    ).all()

    if not issues:
        logger.info("%s: no closed issues need backfilling", project.name)
        return

    logger.info("%s: backfilling %d closed issues", project.name, len(issues))

    try:
        repo = gh.get_repo(f"canonical/{project.name}")
    except pygithub.GithubException:
        logger.exception("Could not access canonical/%s", project.name)
        return

    updated = 0
    skipped = 0
    errors = 0
    started_at = time.monotonic()

    for i, (issue_id, external_id) in enumerate(issues, 1):
        if i % _BATCH_LOG_INTERVAL == 0:
            elapsed = time.monotonic() - started_at
            logger.info(
                "  %s: processed %d/%d (updated=%d skipped=%d errors=%d elapsed=%.0fs)",
                project.name,
                i,
                len(issues),
                updated,
                skipped,
                errors,
                elapsed,
            )

        try:
            gh_issue = repo.get_issue(int(external_id))
            closing_refs = _fetch_closing_references(gh_issue)
        except pygithub.GithubException as exc:
            logger.warning(
                "  %s#%s: failed to fetch timeline: %s", project.name, external_id, exc
            )
            errors += 1
            continue

        if not closing_refs and not force_all:
            # Issue has no closing PRs — store empty list to mark as fetched,
            # so future runs with --all can skip it.
            pass

        if dry_run:
            if closing_refs:
                logger.info(
                    "  [dry-run] %s#%s: would store %d closing ref(s)",
                    project.name,
                    external_id,
                    len(closing_refs),
                )
            skipped += 1
            continue

        # Merge into existing metadata to avoid overwriting other fields.
        db_issue = session.get(Issue, issue_id)
        if db_issue is None:
            skipped += 1
            continue

        new_meta = dict(db_issue.metadata_ or {})
        new_meta["closing_references"] = closing_refs
        session.execute(
            update(Issue).where(Issue.id == issue_id).values(metadata_=new_meta)
        )
        session.commit()

        if closing_refs:
            logger.debug(
                "  %s#%s: stored %d closing ref(s)",
                project.name,
                external_id,
                len(closing_refs),
            )
        updated += 1

    elapsed = time.monotonic() - started_at
    logger.info(
        "%s: done — updated=%d skipped=%d errors=%d in %.0fs",
        project.name,
        updated,
        skipped,
        errors,
        elapsed,
    )


if __name__ == "__main__":
    main()
