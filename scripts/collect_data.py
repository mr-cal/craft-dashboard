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
import time
from dataclasses import dataclass, field

import click
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

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
from craft_dashboard.models.project import Project
from craft_dashboard.models.refresh_schedule import RefreshSchedule
from craft_dashboard.settings import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class CollectionStats:
    """Collection summary for logging."""

    projects_processed: set[str] = field(default_factory=set)
    issues_collected: int = 0

    def merge(self, other: "CollectionStats") -> None:
        """Merge another collection summary into this one."""
        self.projects_processed.update(other.projects_processed)
        self.issues_collected += other.issues_collected


def _format_duration(seconds: float) -> str:
    """Format elapsed seconds for human-readable logs."""
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if hours or minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


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


def _get_dep_branches(
    dep_collector: "DependencyCollector",
    project_name: str,
    hotfix_min_version: str | None,
) -> list[str]:
    """Return the list of branches to collect dependencies for.

    Always includes ``"main"``.  Also discovers ``hotfix/*`` branches and keeps
    only the latest one per major version, subject to ``hotfix_min_version``.

    Args:
        dep_collector: A DependencyCollector instance (holds the GitHub client).
        project_name: Repository name within the configured org.
        hotfix_min_version: Minimum version string for hotfix branches (e.g.
            ``"3.0.0"``).  Hotfix branches whose base version is older than
            this are excluded.  Pass ``None`` to include all hotfix branches.

    Returns:
        Sorted list of branch names starting with ``"main"``.

    """
    from packaging.version import Version  # noqa: PLC0415

    branches = ["main"]
    try:
        repo = dep_collector.gh.get_repo(f"{dep_collector.org}/{project_name}")
        all_branches = [b.name for b in repo.get_branches()]
        hotfix_branches = [b for b in all_branches if b.startswith("hotfix/")]

        # Keep the latest hotfix branch per major version.
        latest_per_major: dict[int, tuple[Version, str]] = {}
        for branch_name in hotfix_branches:
            ver_str = branch_name.split("/", 1)[1]
            try:
                ver = Version(ver_str)
            except Exception:  # noqa: BLE001
                continue
            if hotfix_min_version:
                try:
                    if ver < Version(hotfix_min_version):
                        continue
                except Exception:  # noqa: BLE001
                    pass
            major = ver.major
            if major not in latest_per_major or ver > latest_per_major[major][0]:
                latest_per_major[major] = (ver, branch_name)

        branches += sorted(b for _, b in latest_per_major.values())
    except Exception:  # noqa: BLE001
        logger.warning("Could not list branches for %s", project_name, exc_info=True)
    return branches


async def _collect_github(
    settings: Settings,
    config: object,
    session_factory: object,
    limit: int = 0,
    projects: list[str] | None = None,
    run_started_at: float | None = None,
) -> CollectionStats:
    """Run GitHub data collection for all projects due for refresh."""
    collector = GitHubCollector(
        token=settings.github_token,
        org="canonical",
        maintainers=config.maintainers,
    )
    stats = CollectionStats()
    bots = set(getattr(config, "bots", []))

    project_list = projects if projects else config.craft_projects
    for i, project_name in enumerate(project_list):
        async with session_factory() as session:
            category = (
                "application"
                if project_name in config.craft_applications
                else ("library" if project_name in config.craft_libraries else "other")
            )
            project_id = await _get_or_create_project(
                session, project_name, category, i
            )
            stats.projects_processed.add(project_name)
            project_started_at = time.monotonic()
            elapsed = ""
            if run_started_at is not None:
                elapsed = (
                    f" (elapsed: {_format_duration(time.monotonic() - run_started_at)})"
                )

            logger.info("Collecting GitHub data for %s%s", project_name, elapsed)

            # Always collect dependencies (independent of refresh schedule)
            dep_collector = DependencyCollector(
                token=settings.github_token,
                org="canonical",
                craft_libraries=config.craft_libraries,
            )
            try:
                dep_started_at = time.monotonic()
                branches = _get_dep_branches(
                    dep_collector,
                    project_name,
                    config.hotfix_min_versions.get(project_name),
                )
                dependency_count = await dep_collector.collect_dependencies(
                    project_name,
                    project_id,
                    branches,
                    session,
                )
                logger.info(
                    "  canonical/%s: dependencies collected (%d dependencies) in %s",
                    project_name,
                    dependency_count,
                    _format_duration(time.monotonic() - dep_started_at),
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Failed to collect dependencies for %s",
                    project_name,
                    exc_info=True,
                )

            if project_name in config.craft_applications:
                try:
                    releases_started_at = time.monotonic()
                    release_count = await collector.collect_releases(
                        project_name,
                        project_id,
                        session,
                        hotfix_min_version=config.hotfix_min_versions.get(project_name),
                    )
                    logger.info(
                        "  canonical/%s: releases collected (%d branches) in %s",
                        project_name,
                        release_count,
                        _format_duration(time.monotonic() - releases_started_at),
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Failed to collect releases for %s",
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
                logger.info(
                    "Completed GitHub data for %s in %s (full refresh skipped)",
                    project_name,
                    _format_duration(time.monotonic() - project_started_at),
                )
                continue

            try:
                issues_started_at = time.monotonic()
                issues_collected = await collector.collect_issues(
                    project_name,
                    project_id,
                    session,
                    limit=limit,
                    refresh_age_days=settings.refresh_age_days,
                )
                stats.issues_collected += issues_collected
                logger.info(
                    "  canonical/%s: issues collection completed in %s (%d issues collected)",
                    project_name,
                    _format_duration(time.monotonic() - issues_started_at),
                    issues_collected,
                )

                snapshot_started_at = time.monotonic()
                await generate_snapshot(
                    project_id,
                    session,
                    set(config.maintainers),
                    bots=bots,
                )
                logger.info(
                    "  Generated snapshot for %s in %s",
                    project_name,
                    _format_duration(time.monotonic() - snapshot_started_at),
                )

                await update_refresh_schedule(
                    project_id, "github", config.refresh_interval_days, session
                )
                logger.info(
                    "Completed GitHub data for %s in %s",
                    project_name,
                    _format_duration(time.monotonic() - project_started_at),
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

    return stats


async def _collect_launchpad(
    config: object,
    session_factory: object,
    projects: list[str] | None = None,
    run_started_at: float | None = None,
) -> CollectionStats:
    """Run Launchpad data collection for all configured projects.

    Creates a separate project entry like "snapcraft (launchpad)" for each
    Launchpad project so it appears as a distinct series in trends charts.
    """
    collector = LaunchpadCollector(
        projects=config.launchpad_projects,
        launchpad_maintainers=config.launchpad_maintainers,
    )
    stats = CollectionStats()
    bots = set(getattr(config, "bots", []))

    last_order = len(config.craft_projects)
    lp_list = [
        p for p in config.launchpad_projects if projects is None or p in projects
    ]
    for i, lp_name in enumerate(lp_list):
        async with session_factory() as session:
            lp_project_name = f"{lp_name} (launchpad)"
            project_id = await _get_or_create_project(
                session, lp_project_name, "launchpad", last_order + i
            )
            stats.projects_processed.add(lp_name)
            elapsed = ""
            if run_started_at is not None:
                elapsed = (
                    f" (elapsed: {_format_duration(time.monotonic() - run_started_at)})"
                )

            logger.info("Collecting Launchpad data for %s%s", lp_name, elapsed)
            bugs_started_at = time.monotonic()
            bug_count = await collector.collect_bugs(lp_name, project_id, session)
            stats.issues_collected += bug_count
            logger.info(
                "  %s: %d bugs fetched in %s",
                lp_project_name,
                bug_count,
                _format_duration(time.monotonic() - bugs_started_at),
            )

            snapshot_started_at = time.monotonic()
            await generate_snapshot(
                project_id,
                session,
                set(config.launchpad_maintainers),
                bots=bots,
            )
            logger.info(
                "  Generated snapshot for %s in %s",
                lp_project_name,
                _format_duration(time.monotonic() - snapshot_started_at),
            )

    return stats


async def _main(
    source: str,
    limit: int,
    projects: list[str],
    verbose: bool,  # noqa: FBT001
) -> None:
    """Run data collection."""
    settings = Settings()

    log_level = (
        logging.DEBUG
        if verbose
        else getattr(logging, settings.log_level.upper(), logging.INFO)
    )
    logging.getLogger().setLevel(log_level)

    config = load_config(pathlib.Path(settings.config_file))
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)

    project_filter = list(projects) if projects else None
    if limit:
        logger.info("Issue collection limit: %d per repo", limit)
    if project_filter:
        logger.info("Project filter: %s", project_filter)

    run_started_at = time.monotonic()
    stats = CollectionStats()

    try:
        if source in ("all", "github"):
            stats.merge(
                await _collect_github(
                    settings,
                    config,
                    session_factory,
                    limit=limit,
                    projects=project_filter,
                    run_started_at=run_started_at,
                )
            )
        if source in ("all", "launchpad"):
            stats.merge(
                await _collect_launchpad(
                    config,
                    session_factory,
                    projects=project_filter,
                    run_started_at=run_started_at,
                )
            )
        logger.info(
            "Collection complete: %d projects processed, %d issues collected, total time: %s",
            len(stats.projects_processed),
            stats.issues_collected,
            _format_duration(time.monotonic() - run_started_at),
        )
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
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable debug logging (individual issues, API calls). Overrides LOG_LEVEL.",
)
def main(
    source: str,
    limit: int,
    projects: tuple[str, ...],
    verbose: bool,  # noqa: FBT001
) -> None:
    """Collect data from external sources."""
    asyncio.run(_main(source, limit, list(projects), verbose))


if __name__ == "__main__":
    main()
