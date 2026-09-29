"""Database writes backing GitHub release collection."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.models.release import Release


async def upsert_release(
    session: AsyncSession,
    *,
    project_id: int,
    version: str,
    branch: str,
    released_at: datetime | None,
    is_hotfix: bool,
    metadata: dict,
) -> None:
    """Stage one release row per project and branch.

    Metadata is deliberately left out of the conflict update so previously
    computed commits_since_tag and tag_on_main survive a failed git compare.
    """
    from sqlalchemy.dialects.postgresql import (  # noqa: PLC0415
        insert,
    )

    stmt = insert(Release).values(
        project_id=project_id,
        version=version,
        branch=branch,
        released_at=released_at,
        is_hotfix=is_hotfix,
        metadata_=metadata,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "branch"],
        set_={
            "version": stmt.excluded.version,
            "released_at": stmt.excluded.released_at,
            "is_hotfix": stmt.excluded.is_hotfix,
        },
    )
    await session.execute(stmt)


async def fetch_release_metadata(
    session: AsyncSession, project_id: int, branch: str
) -> dict:
    """Return the stored metadata for a branch's release row, or an empty dict."""
    result = await session.execute(
        sa.select(Release.metadata_).where(
            Release.project_id == project_id,
            Release.branch == branch,
        )
    )
    return result.scalar_one_or_none() or {}


async def update_release_metadata(
    session: AsyncSession, project_id: int, branch: str, metadata: dict
) -> None:
    """Stage a metadata update for a branch's release row."""
    await session.execute(
        sa.update(Release)
        .where(Release.project_id == project_id, Release.branch == branch)
        .values(metadata_=metadata)
    )
