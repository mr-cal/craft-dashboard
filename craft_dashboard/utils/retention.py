"""Snapshot data retention utilities."""

import logging
from datetime import date, timedelta

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
    cutoff = date.today() - timedelta(days=retention_days)
    result = await session.execute(
        delete(Snapshot).where(Snapshot.snapshot_date < cutoff)
    )
    await session.commit()
    deleted = result.rowcount or 0
    if deleted:
        logger.info("Pruned %d snapshots older than %s", deleted, cutoff)
    return deleted
