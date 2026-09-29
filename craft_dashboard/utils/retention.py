"""Snapshot data retention utilities.

Snapshots are the only record of what the issue counts were on a past day;
regenerating them requires a full replay of every issue's history. The whole
table is a couple of megabytes, so nothing prunes it on a schedule. This
exists for the rare case of shrinking a database by hand, and the caller
chooses the window deliberately.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.models.snapshot import Snapshot

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 365


async def prune_old_snapshots(
    session: AsyncSession,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> int:
    """Delete snapshots older than retention_days and return the row count."""
    cutoff = datetime.now(UTC).date() - timedelta(days=retention_days)
    result = await session.execute(
        delete(Snapshot).where(Snapshot.snapshot_date < cutoff)
    )
    await session.commit()
    deleted = getattr(result, "rowcount", 0) or 0
    if deleted:
        logger.info("Pruned %d snapshots older than %s", deleted, cutoff)
    return deleted
