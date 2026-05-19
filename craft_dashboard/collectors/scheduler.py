"""Refresh scheduler for spreading data collection across time."""

import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)


def is_due_for_refresh(next_refresh_at: datetime | None) -> bool:
    """Check if a project is due for a data refresh.

    Args:
        next_refresh_at: The scheduled refresh time, or None if never scheduled.

    Returns:
        True if the project should be refreshed now.

    """
    if next_refresh_at is None:
        return True
    return datetime.now(tz=UTC) >= next_refresh_at


def distribute_refresh_dates(
    project_ids: list[int],
    interval_days: int,
) -> list[tuple[int, datetime]]:
    """Distribute refresh dates evenly across an interval.

    Spreads projects across the interval so API calls are distributed
    over time rather than all happening at once.

    Args:
        project_ids: List of project database IDs.
        interval_days: Number of days over which to spread refreshes.

    Returns:
        List of (project_id, next_refresh_at) tuples.

    """
    if not project_ids:
        return []

    now = datetime.now(tz=UTC)
    total_seconds = interval_days * 86400
    interval = total_seconds / len(project_ids)

    return [
        (pid, now + timedelta(seconds=interval * (i + 1)))
        for i, pid in enumerate(project_ids)
    ]


async def update_refresh_schedule(
    project_id: int,
    source: str,
    interval_days: int,
    session,  # noqa: ANN001
) -> None:
    """Mark a project as successfully refreshed and schedule the next refresh.

    Clears any recorded error state on success.

    Args:
        project_id: The database ID of the project.
        source: The data source ('github' or 'launchpad').
        interval_days: Days until next refresh.
        session: An async SQLAlchemy session.

    """
    from sqlalchemy.dialects.postgresql import insert  # noqa: PLC0415

    from craft_dashboard.models.refresh_schedule import RefreshSchedule  # noqa: PLC0415

    now = datetime.now(tz=UTC)

    stmt = insert(RefreshSchedule).values(
        project_id=project_id,
        source=source,
        last_refreshed_at=now,
        next_refresh_at=now + timedelta(days=interval_days),
        last_error=None,
        consecutive_failures=0,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "source"],
        set_={
            "last_refreshed_at": now,
            "next_refresh_at": now + timedelta(days=interval_days),
            "last_error": None,
            "consecutive_failures": 0,
        },
    )
    await session.execute(stmt)
    await session.commit()

    logger.info(
        "Updated refresh schedule for project_id=%d source=%s",
        project_id,
        source,
    )


async def record_refresh_error(
    project_id: int,
    source: str,
    error: str,
    session,  # noqa: ANN001
) -> None:
    """Record a collection failure and increment the consecutive failure counter.

    Args:
        project_id: The database ID of the project.
        source: The data source ('github' or 'launchpad').
        error: The error message to record.
        session: An async SQLAlchemy session.

    """
    from sqlalchemy import update  # noqa: PLC0415

    from craft_dashboard.models.refresh_schedule import RefreshSchedule  # noqa: PLC0415

    # Increment failures and record error
    stmt = (
        update(RefreshSchedule)
        .where(
            RefreshSchedule.project_id == project_id,
            RefreshSchedule.source == source,
        )
        .values(
            last_error=error,
            consecutive_failures=RefreshSchedule.consecutive_failures + 1,
        )
    )
    result = await session.execute(stmt)

    # If no row existed yet, insert one
    if result.rowcount == 0:
        from sqlalchemy.dialects.postgresql import insert  # noqa: PLC0415

        stmt = insert(RefreshSchedule).values(
            project_id=project_id,
            source=source,
            last_error=error,
            consecutive_failures=1,
        )
        stmt = stmt.on_conflict_do_nothing()
        await session.execute(stmt)

    await session.commit()
    logger.warning(
        "Recorded refresh error for project_id=%d source=%s: %s",
        project_id,
        source,
        error,
    )
