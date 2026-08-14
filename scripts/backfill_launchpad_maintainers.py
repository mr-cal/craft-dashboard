#!/usr/bin/env python3
r"""Backfill Issue.author_is_maintainer for existing Launchpad issues.

The Launchpad collector only recomputes ``author_is_maintainer`` when a bug
is re-fetched (incremental collection only touches recently modified bugs),
so updates to the ``launchpad-maintainers`` config list don't retroactively
apply to already-collected bugs. This script recomputes the flag for every
Launchpad issue using the current config, so config fixes (e.g. adding a
missing maintainer, or fixing a "~"-prefix typo) take effect immediately
instead of waiting for each bug to be touched again upstream.

Usage (production):
    podman exec -i vps-infra_craft-dashboard_1 \
        /app/.venv/bin/python /app/scripts/backfill_launchpad_maintainers.py

Usage (local):
    uv run scripts/backfill_launchpad_maintainers.py
    uv run scripts/backfill_launchpad_maintainers.py --dry-run
"""

import logging
import sys
from pathlib import Path

import click
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent.parent))

from craft_dashboard.config import load_config
from craft_dashboard.models.issue import Issue
from craft_dashboard.settings import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Report what would change but do not write to the database.",
)
def main(dry_run: bool) -> None:
    """Recompute author_is_maintainer for every Launchpad issue."""
    settings = Settings()
    config = load_config(Path(settings.config_file))
    maintainers = set(config.launchpad_maintainers)
    logger.info("launchpad-maintainers: %s", sorted(maintainers))

    sync_db_url = settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    engine = create_engine(sync_db_url)

    if dry_run:
        logger.info("Dry-run mode — no database writes.")

    changed = 0
    checked = 0
    with Session(engine) as session:
        rows = session.execute(
            select(Issue.id, Issue.author, Issue.author_is_maintainer).where(
                Issue.source == "launchpad"
            )
        ).all()

        for issue_id, author, current_value in rows:
            checked += 1
            new_value = author in maintainers if author else False
            if new_value == current_value:
                continue
            changed += 1
            logger.info(
                "issue id=%d author=%s: author_is_maintainer %s -> %s",
                issue_id,
                author,
                current_value,
                new_value,
            )
            if not dry_run:
                session.execute(
                    update(Issue)
                    .where(Issue.id == issue_id)
                    .values(author_is_maintainer=new_value)
                )

        if not dry_run:
            session.commit()

    logger.info(
        "done — checked=%d changed=%d%s",
        checked,
        changed,
        " (dry-run, no writes)" if dry_run else "",
    )


if __name__ == "__main__":
    main()
