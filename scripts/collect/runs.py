"""Collection run health records and the per-source concurrency guard."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from craft_dashboard.models.collection_run import CollectionRun
from sqlalchemy import select

logger = logging.getLogger(__name__)


# Two crons legitimately share a source (e.g. the 10-minute "open" sweep and
# the daily schedule-gated "full" refresh both use source="github") and can
# start in the same minute. Rather than aborting the whole invocation on the
# very first observed conflict — which would silently and permanently starve
# the "full" refresh if it keeps losing that race — poll briefly for the
# other, typically short-lived run to finish before giving up.
_CONCURRENCY_WAIT_TIMEOUT = timedelta(minutes=6)
_CONCURRENCY_POLL_INTERVAL = timedelta(seconds=15)


async def _wait_for_source_available(
    session_factory: object,
    source_name: str,
    *,
    wait_timeout: timedelta = _CONCURRENCY_WAIT_TIMEOUT,
    poll_interval: timedelta = _CONCURRENCY_POLL_INTERVAL,
) -> CollectionRun | None:
    """Wait for any in-progress run for ``source_name`` to clear.

    Returns ``None`` once the source is free to use. If a conflicting run is
    still active after ``wait_timeout`` has elapsed, returns that run so the
    caller can abort as before.
    """
    deadline = datetime.now(UTC) + wait_timeout
    first_check = True
    while True:
        existing_running = await _get_running_collection_run(
            session_factory, source_name
        )
        if existing_running is None:
            return None
        if datetime.now(UTC) >= deadline:
            return existing_running
        if first_check:
            logger.info(
                "Collection run %s in progress for %s (started %s); "
                "waiting up to %s for it to finish before giving up",
                existing_running.id,
                source_name,
                existing_running.started_at,
                wait_timeout,
            )
            first_check = False
        await asyncio.sleep(poll_interval.total_seconds())


async def _create_collection_run(source: str, session_factory: object) -> CollectionRun:
    """Create a running collection health record."""
    async with session_factory() as session:
        run = CollectionRun(
            source=source,
            started_at=datetime.now(UTC),
            status="running",
            projects_processed=0,
            issues_collected=0,
            errors=[],
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


async def _get_running_collection_run(
    session_factory: object,
    source: str,
    *,
    stale_after: timedelta = timedelta(hours=6),
) -> CollectionRun | None:
    """Return an in-progress, non-stale collection run for this source, if any.

    A ``status="running"`` row older than ``stale_after`` is treated as
    abandoned (e.g. from a crashed prior invocation that never reached its
    cleanup) rather than a genuine active conflict, so a single crash can't
    permanently lock out future runs. The cutoff is intentionally generous
    because GitHub ``wait_for_rate_limit`` backoff can keep a healthy run alive
    for a long time.
    """
    async with session_factory() as session:
        cutoff = datetime.now(UTC) - stale_after
        result = await session.execute(
            select(CollectionRun).where(
                CollectionRun.source == source,
                CollectionRun.status == "running",
                CollectionRun.started_at >= cutoff,
            )
        )
        running_run = result.scalars().first()
        if running_run is not None:
            return running_run

        stale_result = await session.execute(
            select(CollectionRun).where(
                CollectionRun.source == source,
                CollectionRun.status == "running",
                CollectionRun.started_at < cutoff,
            )
        )
        stale_run = stale_result.scalars().first()
        if stale_run is not None:
            logger.warning(
                "Ignoring stale collection run %s from %s; treating it as abandoned",
                stale_run.id,
                stale_run.started_at,
            )
        return None


async def _finish_collection_run(
    run: CollectionRun,
    session_factory: object,
    *,
    status: str,
    projects_processed: int,
    issues_collected: int,
    errors: list[dict[str, Any]],
) -> None:
    """Finalize a collection health record with stats and errors."""
    async with session_factory() as session:
        db_run = await session.get(CollectionRun, run.id)
        if db_run is None:
            return

        finished_at = datetime.now(UTC)
        db_run.finished_at = finished_at
        db_run.status = status
        db_run.projects_processed = projects_processed
        db_run.issues_collected = issues_collected
        db_run.errors = errors
        db_run.duration_seconds = (finished_at - db_run.started_at).total_seconds()
        await session.commit()
