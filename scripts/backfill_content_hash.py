#!/usr/bin/env python3
r"""Backfill Issue.content_hash for existing issues.

Populates ``Issue.content_hash`` for every row that doesn't have one yet
(fresh column added by the "add issue content_hash" migration), computed
from each issue's current title/body/state/labels/comments/PR-review-details
using the same hash the collectors now compute on every create/update (see
``craft_dashboard.llm.content_hash.compute_content_hash``).

Without this, ``Issue.content_hash`` stays NULL for pre-existing issues,
which the pending-evaluation query would otherwise need to special-case.

Usage (production):
    podman exec -i vps-infra_craft-dashboard_1 \
        /app/.venv/bin/python /app/scripts/backfill_content_hash.py

Usage (local):
    uv run scripts/backfill_content_hash.py
    uv run scripts/backfill_content_hash.py --project charmcraft
    uv run scripts/backfill_content_hash.py --dry-run
    uv run scripts/backfill_content_hash.py --all  # recompute even if already set
"""

import logging
import sys
import time
from pathlib import Path

import click
from sqlalchemy import create_engine, or_, select, update
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent.parent))

from craft_dashboard.llm.content_hash import compute_content_hash
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.project import Project
from craft_dashboard.settings import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_BATCH_LOG_INTERVAL = 200
_BATCH_SIZE = 500


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
    help="Compute hashes but do not write to the database.",
)
@click.option(
    "--all",
    "force_all",
    is_flag=True,
    help="Recompute content_hash for every issue, not just ones missing it.",
)
def main(projects: tuple[str, ...], dry_run: bool, force_all: bool) -> None:
    """Backfill content_hash for issues missing it (or all, with --all)."""
    settings = Settings()
    sync_db_url = settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    engine = create_engine(sync_db_url)

    if dry_run:
        logger.info("Dry-run mode — no database writes.")
    if force_all:
        logger.info("Force-all mode — recomputing content_hash for every issue.")

    with Session(engine) as session:
        project_filter = list(projects) if projects else None
        project_rows = session.execute(select(Project)).scalars().all()
        if project_filter:
            project_rows = [p for p in project_rows if p.name in project_filter]

        for project in project_rows:
            _backfill_project(
                session=session, project=project, dry_run=dry_run, force_all=force_all
            )


def _backfill_project(
    *, session: Session, project: Project, dry_run: bool, force_all: bool
) -> None:
    """Backfill content_hash for one project's issues."""
    where = [Issue.project_id == project.id]
    if not force_all:
        where.append(or_(Issue.content_hash.is_(None), Issue.content_hash == ""))

    total = session.scalar(select(Issue.id).where(*where).order_by(Issue.id).limit(1))
    if total is None:
        logger.info("%s: no issues need backfilling", project.name)
        return

    updated = 0
    processed = 0
    started_at = time.monotonic()
    last_id = 0

    while True:
        batch = session.execute(
            select(
                Issue.id,
                Issue.title,
                Issue.body,
                Issue.state,
                Issue.labels,
                Issue.comments,
                Issue.metadata_,
            )
            .where(*where, Issue.id > last_id)
            .order_by(Issue.id)
            .limit(_BATCH_SIZE)
        ).all()
        if not batch:
            break

        for issue_id, title, body, state, labels, comments, metadata_ in batch:
            last_id = issue_id
            processed += 1
            new_hash = compute_content_hash(
                title,
                body,
                state,
                labels or [],
                comments or [],
                pr_details=metadata_ or None,
            )
            if not dry_run:
                session.execute(
                    update(Issue)
                    .where(Issue.id == issue_id)
                    .values(content_hash=new_hash)
                )
            updated += 1

            if processed % _BATCH_LOG_INTERVAL == 0:
                elapsed = time.monotonic() - started_at
                logger.info(
                    "  %s: processed %d (elapsed=%.0fs)",
                    project.name,
                    processed,
                    elapsed,
                )

        if not dry_run:
            session.commit()

    elapsed = time.monotonic() - started_at
    logger.info(
        "%s: done — updated=%d in %.0fs%s",
        project.name,
        updated,
        elapsed,
        " (dry-run, no writes)" if dry_run else "",
    )


if __name__ == "__main__":
    main()
