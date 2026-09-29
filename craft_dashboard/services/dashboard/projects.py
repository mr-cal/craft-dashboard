"""Project lookup and category resolution for dashboard views."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from craft_dashboard.models.project import Project

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from craft_dashboard.config import DashboardConfig

LAUNCHPAD_OTHER_PROJECT = "snapcraft (launchpad)"


async def fetch_tracked_projects(session: AsyncSession) -> Sequence[Project]:
    """Fetch non-aggregate projects in display order."""
    result = await session.execute(
        select(Project)
        .where(Project.category != "aggregate")
        .order_by(Project.display_order)
    )
    return result.scalars().all()


def resolve_project_category(project: Project, config: DashboardConfig) -> str:
    """Return the configured category for a project, falling back to its own."""
    if config.craft_applications and project.name in config.craft_applications:
        return "application"
    if config.craft_libraries and project.name in config.craft_libraries:
        return "library"
    if (
        config.craft_other
        and project.name in config.craft_other
        or project.name == LAUNCHPAD_OTHER_PROJECT
    ):
        return "other"
    return project.category
