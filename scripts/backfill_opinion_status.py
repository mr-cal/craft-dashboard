#!/usr/bin/env python3
r"""Backfill state for Launchpad bugs with status "Opinion".

Launchpad's "Opinion" status is considered a *closed* state (the reporter's
concern was heard but no action will be taken), but
``craft_dashboard.collectors.launchpad`` previously treated it as *open*.
This script corrects historical ``Issue`` rows collected before that fix:
any Launchpad issue whose stored ``metadata_->>'status'`` is "Opinion" but
whose ``state`` is still "open" is updated to "closed", and its
``content_hash`` is recomputed so future evaluation runs don't wrongly skip
these issues after the state change.

Usage (production):
    podman exec -i vps-infra_craft-dashboard_1 \
        /app/.venv/bin/python /app/scripts/backfill_opinion_status.py

Usage (local):
    uv run scripts/backfill_opinion_status.py
    uv run scripts/backfill_opinion_status.py --project charmcraft
    uv run scripts/backfill_opinion_status.py --dry-run
"""

import logging
import sys
import time
from pathlib import Path

import click
from sqlalchemy import create_engine, select, update
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
    help="Compute updates but do not write to the database.",
)
def main(projects: tuple[str, ...], dry_run: bool) -> None:
    """Backfill state for Launchpad 'Opinion' bugs stuck as 'open'."""
    settings = Settings()
    sync_db_url = settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    engine = create_engine(sync_db_url)

    if dry_run:
        logger.info("Dry-run mode — no database writes.")

    with Session(engine) as session:
        project_filter = list(projects) if projects else None
        project_rows = session.execute(select(Project)).scalars().all()
        if project_filter:
            project_rows = [p for p in project_rows if p.name in project_filter]

        for project in project_rows:
            _backfill_project(session=session, project=project, dry_run=dry_run)


def _backfill_project(*, session: Session, project: Project, dry_run: bool) -> None:
    """Backfill Opinion-status Launchpad issues for one project."""
    where = [
        Issue.project_id == project.id,
        Issue.source == "launchpad",
        Issue.state == "open",
        Issue.metadata_["status"].astext == "Opinion",
    ]

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

        for issue_id, title, body, labels, comments, metadata_ in batch:
            last_id = issue_id
            processed += 1
            new_hash = compute_content_hash(
                title,
                body,
                "closed",
                labels or [],
                comments or [],
                pr_details=metadata_ or None,
            )
            if not dry_run:
                session.execute(
                    update(Issue)
                    .where(Issue.id == issue_id)
                    .values(state="closed", content_hash=new_hash)
                )
            updated += 1

        if not dry_run:
            session.commit()

    if processed:
        elapsed = time.monotonic() - started_at
        logger.info(
            "%s: done — updated=%d/%d in %.0fs%s",
            project.name,
            updated,
            processed,
            elapsed,
            " (dry-run, no writes)" if dry_run else "",
        )
    else:
        logger.info("%s: no Opinion-status issues need backfilling", project.name)


if __name__ == "__main__":
    main()
