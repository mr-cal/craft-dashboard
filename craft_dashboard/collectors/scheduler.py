"""Refresh scheduler for spreading data collection across time."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "get_least_recently_refreshed",
    "is_due_for_refresh",
    "record_refresh_error",
    "update_refresh_schedule",
]

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


async def get_least_recently_refreshed(
    session: AsyncSession,
) -> tuple[int, str, str] | None:
    """Return the (project_id, project_name, source) most overdue for a full refresh.

    Used by the hourly continuous rotation (``--mode rotation`` in
    ``scripts/collect_data.py``) to pick exactly one project+source pair per
    cron tick, replacing the old weekly-distributed ``next_refresh_at``
    schedule. Every non-aggregate project (GitHub *and* Launchpad) is
    eligible, ordered by ``RefreshSchedule.last_refreshed_at`` ascending —
    projects never refreshed sort first, then the least-recently-refreshed
    project, so repeated calls cycle round-robin through the full project
    list over time.

    A project's source is derived from ``Project.category`` (Launchpad
    projects are created with ``category="launchpad"``; everything else is
    GitHub) rather than a dedicated column, matching how
    ``scripts/collect_data.py`` creates project rows.

    Returns:
        None if there are no non-aggregate projects at all.

    """
    from craft_dashboard.models.project import Project
    from craft_dashboard.models.refresh_schedule import RefreshSchedule

    projects_result = await session.execute(
        select(Project.id, Project.name, Project.category).where(
            Project.category != "aggregate"
        )
    )
    projects = list(projects_result.all())
    if not projects:
        return None

    schedules_result = await session.execute(
        select(
            RefreshSchedule.project_id,
            RefreshSchedule.source,
            RefreshSchedule.last_refreshed_at,
        )
    )
    last_refreshed: dict[tuple[int, str], datetime | None] = {
        (row.project_id, row.source): row.last_refreshed_at for row in schedules_result
    }
    never = datetime.min.replace(tzinfo=UTC)

    def sort_key(row: Row) -> tuple[int, datetime, str]:
        source = "launchpad" if row.category == "launchpad" else "github"
        last = last_refreshed.get((row.id, source))
        if last is None:
            return (0, never, row.name)
        return (1, last, row.name)

    best = min(projects, key=sort_key)
    source = "launchpad" if best.category == "launchpad" else "github"
    return best.id, best.name, source


async def update_refresh_schedule(
    project_id: int,
    source: str,
    interval_days: int,
    session: AsyncSession,
) -> None:
    """Mark a project as successfully refreshed and schedule the next refresh.

    Clears any recorded error state on success.

    Args:
        project_id: The database ID of the project.
        source: The data source ('github' or 'launchpad').
        interval_days: Days until next refresh.
        session: An async SQLAlchemy session.

    """
    from sqlalchemy.dialects.postgresql import (
        insert,
    )

    from craft_dashboard.models.refresh_schedule import (
        RefreshSchedule,
    )

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
    session: AsyncSession,
) -> None:
    """Record a collection failure and increment the consecutive failure counter.

    Args:
        project_id: The database ID of the project.
        source: The data source ('github' or 'launchpad').
        error: The error message to record.
        session: An async SQLAlchemy session.

    """
    from sqlalchemy import update

    from craft_dashboard.models.refresh_schedule import (
        RefreshSchedule,
    )

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
    rows_updated = getattr(result, "rowcount", 0) or 0

    # If no row existed yet, insert one
    if rows_updated == 0:
        from sqlalchemy.dialects.postgresql import (
            insert,
        )

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
