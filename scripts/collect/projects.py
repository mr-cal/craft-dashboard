"""Project rows and collection watermarks."""

from datetime import UTC, datetime

from craft_dashboard.models.collection_watermark import CollectionWatermark
from craft_dashboard.models.project import Project
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert


async def _get_or_create_project(
    session: object,
    name: str,
    category: str,
    order: int,
) -> int:
    """Get or create a project, returning its ID."""
    stmt = insert(Project).values(
        name=name,
        category=category,
        display_order=order,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["name"],
        set_={
            "category": stmt.excluded.category,
            "display_order": stmt.excluded.display_order,
        },
    )
    await session.execute(stmt)
    await session.commit()

    result = await session.execute(select(Project.id).where(Project.name == name))
    return result.scalar_one()


async def _get_collection_watermark(
    session: object, project_id: int, source: str
) -> datetime | None:
    """Return the latest successful collection timestamp for a project source."""
    result = await session.execute(
        select(CollectionWatermark.last_collected_at).where(
            CollectionWatermark.project_id == project_id,
            CollectionWatermark.source == source,
        )
    )
    return result.scalar_one_or_none()


async def _upsert_collection_watermark(
    session: object,
    project_id: int,
    source: str,
    collected_at: datetime | None = None,
) -> None:
    """Persist the latest successful collection timestamp for a project source."""
    collected_at = collected_at or datetime.now(UTC)
    stmt = insert(CollectionWatermark).values(
        project_id=project_id,
        source=source,
        last_collected_at=collected_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "source"],
        set_={"last_collected_at": stmt.excluded.last_collected_at},
    )
    await session.execute(stmt)
    await session.commit()
