"""Database reads and writes backing GitHub issue collection."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.collectors import ISSUE_UPSERT_FIELDS
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.issue_activity import IssueActivity


def stale_issue_filter(project_id: int, cutoff: datetime) -> sa.ColumnElement[bool]:
    """Build the filter matching issues due for a refresh.

    Args:
        project_id: The database ID of the project.
        cutoff: Issues last fetched before this timestamp are stale.

    Returns:
        A SQLAlchemy boolean expression.

    """
    return sa.and_(
        Issue.project_id == project_id,
        Issue.source == "github",
        sa.or_(
            Issue.last_fetched_at.is_(None),
            Issue.last_fetched_at < cutoff,
        ),
    )


async def count_stale_issues(
    session: AsyncSession, stale_where: sa.ColumnElement[bool]
) -> int:
    """Count issues matching a stale filter."""
    result = await session.execute(
        sa.select(sa.func.count()).select_from(Issue).where(stale_where)
    )
    return result.scalar_one()


async def count_issues(session: AsyncSession, project_id: int) -> int:
    """Count all GitHub issues stored for a project."""
    result = await session.execute(
        sa.select(sa.func.count())
        .select_from(Issue)
        .where(Issue.project_id == project_id, Issue.source == "github")
    )
    return result.scalar_one()


async def count_closed_issues(session: AsyncSession, project_id: int) -> int:
    """Count closed GitHub issues stored for a project."""
    result = await session.execute(
        sa.select(sa.func.count())
        .select_from(Issue)
        .where(
            Issue.project_id == project_id,
            Issue.source == "github",
            Issue.state == "closed",
        )
    )
    return result.scalar_one()


async def oldest_stale_last_fetched(
    session: AsyncSession, stale_where: sa.ColumnElement[bool]
) -> datetime | None:
    """Return the oldest last_fetched_at among stale issues, if any."""
    result = await session.execute(
        sa.select(sa.func.min(Issue.last_fetched_at))
        .select_from(Issue)
        .where(stale_where)
    )
    return result.scalar_one_or_none()


async def fetch_issue_freshness(
    session: AsyncSession, project_id: int, external_id: str
) -> tuple[datetime | None, datetime | None]:
    """Return the stored (last_fetched_at, closed_at) for an issue.

    Args:
        session: An async SQLAlchemy session.
        project_id: The database ID of the project.
        external_id: The GitHub issue number as a string.

    Returns:
        A (last_fetched_at, closed_at) tuple, both None when the issue is new.

    """
    result = await session.execute(
        sa.select(Issue.last_fetched_at, Issue.closed_at).where(
            Issue.project_id == project_id,
            Issue.source == "github",
            Issue.external_id == external_id,
        )
    )
    row = result.one_or_none()
    return (row[0], row[1]) if row else (None, None)


def stage_issue_activity(
    session: AsyncSession,
    *,
    project_id: int,
    issue_number: int,
    change_type: str,
    title: str,
    occurred_at: datetime,
    collection_run_id: int | None,
) -> None:
    """Stage an IssueActivity row without flushing or committing."""
    session.add(
        IssueActivity(
            project_id=project_id,
            issue_number=issue_number,
            change_type=change_type,
            title=title,
            occurred_at=occurred_at,
            collection_run_id=collection_run_id,
        )
    )


async def upsert_issue(
    session: AsyncSession, values: dict, collection_run_id: int | None
) -> None:
    """Stage an issue upsert on the session without committing."""
    from sqlalchemy.dialects.postgresql import (  # noqa: PLC0415
        insert,
    )

    stmt = insert(Issue).values(**values, collection_run_id=collection_run_id)
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "source", "external_id"],
        set_={field: getattr(stmt.excluded, field) for field in ISSUE_UPSERT_FIELDS}
        | {
            "metadata": stmt.excluded.metadata,
            "comments": stmt.excluded.comments,
        },
    )
    await session.execute(stmt)


async def fetch_open_external_ids(session: AsyncSession, project_id: int) -> set[str]:
    """Return the external IDs of issues stored as open for a project."""
    rows = await session.execute(
        sa.select(Issue.external_id).where(
            Issue.project_id == project_id,
            Issue.source == "github",
            Issue.state == "open",
        )
    )
    return {row[0] for row in rows.fetchall()}


async def fetch_issue_content(
    session: AsyncSession, project_id: int, external_id: str
) -> Row | None:
    """Return the stored (title, body, labels, comments, metadata_) for an issue."""
    result = await session.execute(
        sa.select(
            Issue.title,
            Issue.body,
            Issue.labels,
            Issue.comments,
            Issue.metadata_,
        ).where(
            Issue.project_id == project_id,
            Issue.source == "github",
            Issue.external_id == external_id,
        )
    )
    return result.one_or_none()


async def mark_issue_closed(
    session: AsyncSession,
    *,
    project_id: int,
    external_id: str,
    state: str,
    closed_at: datetime,
    content_hash: str | None,
    last_fetched_at: datetime,
    collection_run_id: int | None,
) -> None:
    """Stage the closed/merged state transition for a reconciled issue."""
    await session.execute(
        sa.update(Issue)
        .where(
            Issue.project_id == project_id,
            Issue.source == "github",
            Issue.external_id == external_id,
        )
        .values(
            state=state,
            closed_at=closed_at,
            content_hash=content_hash,
            last_fetched_at=last_fetched_at,
            collection_run_id=collection_run_id,
        )
    )
