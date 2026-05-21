#!/usr/bin/env python3
"""Data collection entry point for cron jobs.

Usage:
    uv run scripts/collect_data.py --source all
    uv run scripts/collect_data.py --source github
    uv run scripts/collect_data.py --source launchpad
    uv run scripts/collect_data.py --source github --limit 25
    uv run scripts/collect_data.py --source github --project snapcraft --project rockcraft
    uv run scripts/collect_data.py --source github --limit 25 --project snapcraft

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

from craft_dashboard.collectors.dependencies import DependencyCollector
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
    limit: int = 0,
    projects: list[str] | None = None,
) -> None:
    """Run GitHub data collection for all projects due for refresh."""
    from sqlalchemy import select

    from craft_dashboard.models.refresh_schedule import RefreshSchedule

    collector = GitHubCollector(
        token=settings.github_token,
        org="canonical",
        maintainers=config.maintainers,
    )

    project_list = projects if projects else config.craft_projects
    for i, project_name in enumerate(project_list):
        async with session_factory() as session:
            category = "application" if project_name in config.craft_applications else (
                "library" if project_name in config.craft_libraries else "other"
            )
            project_id = await _get_or_create_project(
                session, project_name, category, i
            )

            # Always collect dependencies (independent of refresh schedule)
            dep_collector = DependencyCollector(
                token=settings.github_token,
                org="canonical",
            )
            try:
                await dep_collector.collect_dependencies(
                    project_name, project_id, ["main"], session,
                )
            except Exception:
                logger.warning(
                    "Failed to collect dependencies for %s",
                    project_name,
                    exc_info=True,
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
                logger.info("Skipping %s (not due for full refresh)", project_name)
                continue

            logger.info("Collecting GitHub data for %s", project_name)
            try:
                await collector.collect_issues(
                    project_name, project_id, session,
                    limit=limit,
                    refresh_age_days=settings.refresh_age_days,
                )
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


async def _collect_launchpad(config, session_factory, projects: list[str] | None = None) -> None:
    """Run Launchpad data collection for all configured projects."""
    collector = LaunchpadCollector(projects=config.launchpad_projects)

    lp_list = [p for p in config.launchpad_projects if projects is None or p in projects]
    for lp_name in lp_list:
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


async def _main(source: str, limit: int, projects: list[str], verbose: bool) -> None:
    """Run data collection."""
    settings = Settings()

    log_level = logging.DEBUG if verbose else getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)

    config = load_config(pathlib.Path(settings.config_file))
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)

    project_filter = list(projects) if projects else None
    if limit:
        logger.info("Issue collection limit: %d per repo", limit)
    if project_filter:
        logger.info("Project filter: %s", project_filter)

    try:
        if source in ("all", "github"):
            await _collect_github(settings, config, session_factory, limit=limit, projects=project_filter)
        if source in ("all", "launchpad"):
            await _collect_launchpad(config, session_factory, projects=project_filter)
    finally:
        await engine.dispose()


@click.command()
@click.option(
    "--source",
    type=click.Choice(["github", "launchpad", "all"]),
    default="all",
    help="Data source to collect from.",
)
@click.option(
    "--limit",
    default=0,
    type=int,
    help="Max issues to fetch per repository (0 = all). Useful for testing.",
)
@click.option(
    "--project",
    "projects",
    multiple=True,
    help="Only collect data for these projects (repeatable). Default: all configured projects.",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    default=False,
    help="Enable debug logging (individual issues, API calls). Overrides LOG_LEVEL.",
)
def main(source: str, limit: int, projects: tuple[str, ...], verbose: bool) -> None:
    """Collect data from external sources."""
    asyncio.run(_main(source, limit, list(projects), verbose))


if __name__ == "__main__":
    main()
