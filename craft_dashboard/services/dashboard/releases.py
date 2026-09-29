"""Latest-release lookups and the least-recent application spotlight."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from craft_dashboard.models.project import Project
from craft_dashboard.models.release import Release
from craft_dashboard.services.dashboard.badges import compute_release_badge_color
from craft_dashboard.services.dashboard.common import days_since

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Select
    from sqlalchemy.ext.asyncio import AsyncSession

    from craft_dashboard.config import DashboardConfig
    from craft_dashboard.services.dashboard.common import ReleaseRow
    from craft_dashboard.services.dashboard.types import AppReleaseSpotlight

UNRELEASED_TAG = "(unreleased)"


def parse_fallback_date(date_str: str | None) -> datetime | None:
    """Parse an ISO date string from configuration into a UTC datetime."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str)
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def build_latest_release_query() -> Select[Any]:
    """Return the query listing releases newest-first with project details."""
    return (
        select(
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            Project.category,
            Project.github_org,
            Release.version,
            Release.released_at,
            Release.metadata_,
        )
        .join(Project, Release.project_id == Project.id)
        .where(Project.category != "aggregate")
        .order_by(Release.released_at.desc().nullslast())
    )


async def fetch_latest_release_by_project(
    session: AsyncSession,
) -> dict[int, Any]:
    """Return the most recent release row per project id."""
    rel_rows = (await session.execute(build_latest_release_query())).all()
    latest_rel_by_project: dict[int, Any] = {}
    for row in rel_rows:
        if row.project_id not in latest_rel_by_project:
            latest_rel_by_project[row.project_id] = row
    return latest_rel_by_project


def resolve_release_summary(
    project: Project,
    rel: ReleaseRow,
    config: DashboardConfig,
    now: datetime,
) -> tuple[str | None, int | None]:
    """Return the version and age in days of a project's latest known release."""
    if rel is not None and rel.released_at is not None:
        return rel.version, days_since(now, rel.released_at)
    fallback_dt = parse_fallback_date(config.initial_release_dates.get(project.name))
    if fallback_dt is not None:
        return (
            config.initial_release_tags.get(project.name, UNRELEASED_TAG),
            days_since(now, fallback_dt),
        )
    return None, None


def _spotlight_for_project(
    project: Project,
    rel: ReleaseRow,
    config: DashboardConfig,
    now: datetime,
) -> AppReleaseSpotlight:
    """Build the release spotlight entry for a single application project."""
    if rel is not None and rel.released_at is not None:
        days_ago = days_since(now, rel.released_at)
        return {
            "project_name": project.name,
            "version": rel.version,
            "released_at": rel.released_at,
            "days_ago": days_ago,
            "badge_color": compute_release_badge_color(days_ago),
            "is_fallback": False,
        }

    fallback_dt = parse_fallback_date(config.initial_release_dates.get(project.name))
    if fallback_dt is not None:
        days_ago = days_since(now, fallback_dt)
        version_tag = config.initial_release_tags.get(project.name, UNRELEASED_TAG)
        return {
            "project_name": project.name,
            "version": version_tag,
            "released_at": fallback_dt,
            "days_ago": days_ago,
            "badge_color": compute_release_badge_color(days_ago),
            "is_fallback": version_tag == UNRELEASED_TAG,
        }

    return {
        "project_name": project.name,
        "version": UNRELEASED_TAG,
        "released_at": None,
        "days_ago": None,
        "badge_color": "neutral",
        "is_fallback": True,
    }


def build_app_spotlights(
    projects: Sequence[Project],
    latest_rel_by_project: dict[int, Any],
    config: DashboardConfig,
    now: datetime,
) -> list[AppReleaseSpotlight]:
    """Build application release spotlights sorted least-recent first."""
    spotlights = [
        _spotlight_for_project(p, latest_rel_by_project.get(p.id), config, now)
        for p in projects
        if p.category == "application" and p.name not in config.hide_releases
    ]
    spotlights.sort(
        key=lambda item: (item["days_ago"] is None, -(item["days_ago"] or 0))
    )
    return spotlights
