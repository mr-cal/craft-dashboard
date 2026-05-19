#!/usr/bin/env python3
"""Data collection entry point for cron jobs.

Usage:
    uv run scripts/collect_data.py --source all
    uv run scripts/collect_data.py --source github
    uv run scripts/collect_data.py --source launchpad

Environment variables:
    DATABASE_URL: PostgreSQL connection URL
    GITHUB_TOKEN: GitHub personal access token
"""

import asyncio
import logging
import pathlib
import sys

import click

# Add project root to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from craft_dashboard.collectors.github import GitHubCollector
from craft_dashboard.collectors.launchpad import LaunchpadCollector
from craft_dashboard.collectors.scheduler import (
    is_due_for_refresh,
    record_refresh_error,
    update_refresh_schedule,
)
from craft_dashboard.collectors.snapshots import generate_snapshot
from craft_dashboard.config import load_config
from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.settings import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _get_or_create_project(session, name: str, category: str, order: int) -> int:
    """Get or create a project, returning its ID."""
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    from craft_dashboard.models.project import Project

    stmt = insert(Project).values(
        name=name,
        category=category,
        display_order=order,
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
    await session.execute(stmt)
    await session.commit()

    result = await session.execute(select(Project.id).where(Project.name == name))
    return result.scalar_one()


async def _collect_github(
    settings: Settings,
    config,
    session_factory,
) -> None:
    """Run GitHub data collection for all projects due for refresh."""
    from sqlalchemy import select

    from craft_dashboard.models.refresh_schedule import RefreshSchedule

    collector = GitHubCollector(
        token=settings.github_token,
        org="canonical",
        maintainers=config.maintainers,
    )

    for i, project_name in enumerate(config.craft_projects):
        async with session_factory() as session:
            category = "application" if project_name in config.craft_applications else (
                "library" if project_name in config.craft_libraries else "other"
            )
            project_id = await _get_or_create_project(
                session, project_name, category, i
            )

            # Check refresh schedule
            result = await session.execute(
                select(RefreshSchedule.next_refresh_at).where(
                    RefreshSchedule.project_id == project_id,
                    RefreshSchedule.source == "github",
                )
            )
            next_refresh = result.scalar_one_or_none()

            if not is_due_for_refresh(next_refresh):
                logger.info("Skipping %s (not due for refresh)", project_name)
                continue

            logger.info("Collecting GitHub data for %s", project_name)
            try:
                await collector.collect_issues(project_name, project_id, session)
                await collector.collect_releases(project_name, project_id, session)
                await generate_snapshot(
                    project_id, session, set(config.maintainers)
                )
                await update_refresh_schedule(
                    project_id, "github", config.refresh_interval_days, session
                )
            except Exception as exc:
                logger.exception("Failed to collect GitHub data for %s", project_name)
                async with session_factory() as err_session:
                    await record_refresh_error(
                        project_id, "github", str(exc), err_session
                    )
                continue

            # Avoid GitHub secondary rate limits between repos
            await asyncio.sleep(1)


async def _collect_launchpad(config, session_factory) -> None:
    """Run Launchpad data collection for all configured projects."""
    collector = LaunchpadCollector(projects=config.launchpad_projects)

    for lp_name in config.launchpad_projects:
        async with session_factory() as session:
            from sqlalchemy import select

            from craft_dashboard.models.project import Project

            result = await session.execute(
                select(Project.id).where(Project.name == lp_name)
            )
            project_id = result.scalar_one_or_none()
            if project_id is None:
                logger.warning("Project %s not found in DB, skipping LP collection", lp_name)
                continue

            logger.info("Collecting Launchpad data for %s", lp_name)
            await collector.collect_bugs(lp_name, project_id, session)


async def _main(source: str) -> None:
    """Run data collection."""
    settings = Settings()
    config = load_config(pathlib.Path(settings.config_file))
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)

    try:
        if source in ("all", "github"):
            await _collect_github(settings, config, session_factory)
        if source in ("all", "launchpad"):
            await _collect_launchpad(config, session_factory)
    finally:
        await engine.dispose()


@click.command()
@click.option(
    "--source",
    type=click.Choice(["github", "launchpad", "all"]),
    default="all",
    help="Data source to collect from.",
)
def main(source: str) -> None:
    """Collect data from external sources."""
    asyncio.run(_main(source))


if __name__ == "__main__":
    main()
