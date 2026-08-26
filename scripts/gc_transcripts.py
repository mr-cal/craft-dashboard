#!/usr/bin/env python3
"""Cron entrypoint: garbage-collect superseded evaluation transcripts.

Run periodically from host cron (see docs/deployment.md and the cron.d entry
in mr-cal/vps-infra). There is no in-process scheduler; this script is the
scheduled job. Idempotent and safe to run as often as desired.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
import sys

# Add project root to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.services.transcript_gc import delete_superseded_transcripts
from craft_dashboard.settings import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _main() -> None:
    settings = Settings()
    engine = get_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    session_factory = get_session_factory(engine)
    try:
        async with session_factory() as session:
            deleted = await delete_superseded_transcripts(
                session,
                retention_days=settings.eval_transcript_retention_days,
            )
        logger.info("Transcript GC: deleted %d superseded transcript(s)", deleted)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
