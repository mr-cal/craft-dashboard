#!/usr/bin/env python3
"""Cron entrypoint: apply every retention policy.

Runs, in order:

1. transcript GC (``eval_transcript_retention_days``)
2. embedding clearing on superseded evaluations
3. superseded evaluation deletion (``eval_retention_days``)
4. snapshot pruning (``snapshot_retention_days``)

Order matters: embeddings are cleared before rows are deleted so the pgvector
index shrinks even for rows still inside the retention window.

Idempotent and safe to run as often as desired. ``--dry-run`` reports what
each policy would remove without writing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.services.evaluation_gc import (
    clear_superseded_embeddings,
    delete_superseded_evaluations,
)
from craft_dashboard.services.transcript_gc import delete_superseded_transcripts
from craft_dashboard.settings import Settings
from craft_dashboard.utils.retention import prune_old_snapshots

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def run_gc(*, dry_run: bool = False) -> None:
    """Apply every retention policy against the configured database."""
    settings = Settings()
    engine = get_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    session_factory = get_session_factory(engine)
    verb = "would remove" if dry_run else "removed"
    try:
        async with session_factory() as session:
            if dry_run:
                logger.info("Dry run: no rows will be written")
            else:
                transcripts = await delete_superseded_transcripts(
                    session,
                    retention_days=settings.eval_transcript_retention_days,
                )
                logger.info("Transcripts: %s %d row(s)", verb, transcripts)

            embeddings = await clear_superseded_embeddings(session, dry_run=dry_run)
            logger.info(
                "Superseded embeddings: %s %d row(s)",
                "would clear" if dry_run else "cleared",
                embeddings,
            )

            evaluations = await delete_superseded_evaluations(
                session,
                retention_days=settings.eval_retention_days,
                dry_run=dry_run,
            )
            logger.info(
                "Superseded evaluations older than %d day(s): %s %d row(s)",
                settings.eval_retention_days,
                verb,
                evaluations,
            )

            if not dry_run:
                snapshots = await prune_old_snapshots(
                    session,
                    retention_days=settings.snapshot_retention_days,
                )
                logger.info("Snapshots: %s %d row(s)", verb, snapshots)
    finally:
        await engine.dispose()


def main() -> None:
    """Parse arguments and run the garbage collector."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what each policy would remove without writing.",
    )
    args = parser.parse_args()
    asyncio.run(run_gc(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
