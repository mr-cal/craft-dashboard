"""GitHub collection pass: dependencies, releases, and issues per project."""

import asyncio
import logging
import time
from datetime import UTC, datetime

from craft_dashboard.collectors import RateLimitError
from craft_dashboard.collectors.dependencies import DependencyCollector
from craft_dashboard.collectors.github import GitHubCollector
from craft_dashboard.collectors.scheduler import (
    is_due_for_refresh,
    record_open_poll_success,
    record_refresh_error,
    update_refresh_schedule,
)
from craft_dashboard.collectors.snapshots import generate_snapshot
from craft_dashboard.models.refresh_schedule import RefreshSchedule
from craft_dashboard.settings import Settings
from github import GithubException
from sqlalchemy import select

from scripts.collect.projects import (
    _get_collection_watermark,
    _get_or_create_project,
    _upsert_collection_watermark,
)
from scripts.collect.reporting import (
    CollectionStats,
    _format_duration,
    _summarize_exception,
)
from scripts.collect.retry import _retry_github

logger = logging.getLogger(__name__)


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
    from packaging.version import InvalidVersion, Version

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
            except InvalidVersion:
                continue
            if hotfix_min_version:
                try:
                    if ver < Version(hotfix_min_version):
                        continue
                except InvalidVersion:
                    logger.debug(
                        "Could not compare hotfix minimum version %r",
                        hotfix_min_version,
                        exc_info=True,
                    )
            major = ver.major
            if major not in latest_per_major or ver > latest_per_major[major][0]:
                latest_per_major[major] = (ver, branch_name)

        branches += sorted(b for _, b in latest_per_major.values())
    except Exception:  # noqa: BLE001 - one project's branch listing must not abort the run
        logger.warning("Could not list branches for %s", project_name, exc_info=True)
    return branches


def _check_initial_rate_limit(collector: GitHubCollector) -> None:
    """Log the starting API quota and refuse to start with an exhausted one."""
    rate_limit = collector.check_rate_limit()
    logger.info(
        "GitHub API quota: %d/%d remaining (reset at %s)",
        rate_limit["core_remaining"],
        rate_limit["core_limit"],
        rate_limit["core_reset"],
    )
    if int(rate_limit["core_remaining"]) <= 0:
        raise RateLimitError(
            resource="core",
            remaining=int(rate_limit["core_remaining"]),
            limit=int(rate_limit["core_limit"]),
        )
    collector.wait_for_rate_limit()


async def _log_rate_limit_after_project(
    collector: GitHubCollector,
    project_name: str,
) -> None:
    """Log the remaining API quota and wait it out if it is running low."""
    try:
        rate_limit = await _retry_github(
            collector.check_rate_limit,
            f"check_rate_limit after {project_name}",
        )
        logger.info(
            "GitHub API quota after %s: %d/%d remaining",
            project_name,
            rate_limit["core_remaining"],
            rate_limit["core_limit"],
        )
        collector.wait_for_rate_limit()
    except GithubException as exc:
        logger.warning(
            "Could not check GitHub rate limit after %s: %s",
            project_name,
            _summarize_exception(exc),
        )


async def _collect_project_dependencies(
    settings: Settings,
    config: object,
    project_name: str,
    project_id: int,
    session: object,
) -> None:
    """Collect one project's dependencies, independent of the refresh schedule."""
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
    except Exception:  # noqa: BLE001 - per-project isolation; other projects still collect
        logger.warning(
            "Failed to collect dependencies for %s",
            project_name,
            exc_info=True,
        )


async def _collect_project_releases(
    collector: GitHubCollector,
    project_name: str,
    project_id: int,
    session: object,
) -> None:
    """Collect one project's releases, independent of the refresh schedule."""
    try:
        releases_started_at = time.monotonic()
        release_count = await _retry_github(
            lambda _pn=project_name, _pid=project_id, _s=session: (
                collector.collect_releases(
                    _pn,
                    _pid,
                    _s,
                )
            ),
            f"collect_releases({project_name})",
        )
        logger.info(
            "  canonical/%s: releases collected (%d branches) in %s",
            project_name,
            release_count,
            _format_duration(time.monotonic() - releases_started_at),
        )
    except Exception:  # noqa: BLE001 - per-project isolation; other projects still collect
        logger.warning(
            "Failed to collect releases for %s",
            project_name,
            exc_info=True,
        )


async def _collect_open_issues(
    collector: GitHubCollector,
    config: object,
    session: object,
    session_factory: object,
    project_name: str,
    project_id: int,
    stats: CollectionStats,
    bots: set[str],
    limit: int,
    collection_run_id: int | None,
) -> None:
    """Run the open-issue pass for one project.

    The open-issue watermark is advanced only once the whole pass — issue
    collection and snapshot generation — has succeeded.
    """
    try:
        open_started_at = time.monotonic()
        open_collection_started_at = datetime.now(UTC)
        open_watermark = await _get_collection_watermark(
            session, project_id, "github_issues_open"
        )
        open_collected = await _retry_github(
            lambda _pn=project_name, _pid=project_id, _s=session, _w=open_watermark: (
                collector.collect_issues(
                    _pn,
                    _pid,
                    _s,
                    limit=limit,
                    state="open",
                    since=_w,
                    collection_run_id=collection_run_id,
                )
            ),
            f"collect_issues(open, {project_name})",
        )
        stats.issues_collected += open_collected
        logger.info(
            "  canonical/%s: open issues collected (%d) in %s",
            project_name,
            open_collected,
            _format_duration(time.monotonic() - open_started_at),
        )
        await _upsert_collection_watermark(
            session,
            project_id,
            "github_issues_open",
            open_collection_started_at,
        )

        snapshot_started_at = time.monotonic()
        await generate_snapshot(
            project_id,
            session,
            set(config.maintainers),
            bots=bots,
            filtered_issue_ids=set(config.filtered_issues.get(project_name, []))
            or None,
        )
        logger.info(
            "  Generated snapshot for %s in %s",
            project_name,
            _format_duration(time.monotonic() - snapshot_started_at),
        )
        async with session_factory() as ok_session:
            await record_open_poll_success(
                project_id,
                "github",
                ok_session,
                issues_collected=open_collected,
            )
    except Exception as exc:
        logger.exception("Failed to collect open GitHub issues for %s", project_name)
        error_summary = _summarize_exception(exc)
        stats.errors.append({"project": project_name, "error": error_summary})
        async with session_factory() as err_session:
            await record_refresh_error(
                project_id,
                "github",
                error_summary,
                err_session,
                kind="open_poll",
            )


async def _is_full_pass_due(
    session: object,
    project_name: str,
    project_id: int,
    project_started_at: float,
    mode: str,
    force_schedule: bool,
) -> bool:
    """Return whether the schedule-gated full pass should run for this project."""
    result = await session.execute(
        select(RefreshSchedule.next_refresh_at).where(
            RefreshSchedule.project_id == project_id,
            RefreshSchedule.source == "github",
        )
    )
    next_refresh = result.scalar_one_or_none()

    if mode == "full" and not force_schedule and not is_due_for_refresh(next_refresh):
        logger.info("Skipping %s (not due for full refresh)", project_name)
        logger.info(
            "Completed GitHub data for %s in %s (full refresh skipped)",
            project_name,
            _format_duration(time.monotonic() - project_started_at),
        )
        return False
    return True


async def _collect_all_issues(
    collector: GitHubCollector,
    settings: Settings,
    config: object,
    session: object,
    session_factory: object,
    project_name: str,
    project_id: int,
    project_started_at: float,
    stats: CollectionStats,
    bots: set[str],
    limit: int,
    collection_run_id: int | None,
    full_refresh: bool,
) -> None:
    """Run the open-plus-closed pass for one project.

    The ``github`` watermark and the refresh schedule are advanced only once
    issue collection and snapshot generation have both succeeded.
    """
    watermark = (
        None
        if full_refresh
        else await _get_collection_watermark(session, project_id, "github")
    )
    is_full = full_refresh or (watermark is None)
    if full_refresh:
        logger.info("  canonical/%s: full collection requested", project_name)
    elif watermark is not None:
        logger.info(
            "  canonical/%s: incremental collection since %s",
            project_name,
            watermark.isoformat(),
        )
    else:
        logger.info(
            "  canonical/%s: full collection (no watermark found)",
            project_name,
        )

    try:
        issues_started_at = time.monotonic()
        issues_collected = await _retry_github(
            lambda _pn=project_name, _pid=project_id, _s=session, _w=watermark, _f=is_full: (
                collector.collect_issues(
                    _pn,
                    _pid,
                    _s,
                    limit=limit,
                    refresh_age_days=settings.refresh_age_days,
                    since=_w,
                    collection_run_id=collection_run_id,
                    state="full" if _f else "all",
                )
            ),
            f"collect_issues({'full' if is_full else 'all'}, {project_name})",
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
            filtered_issue_ids=set(config.filtered_issues.get(project_name, []))
            or None,
        )
        logger.info(
            "  Generated snapshot for %s in %s",
            project_name,
            _format_duration(time.monotonic() - snapshot_started_at),
        )

        await update_refresh_schedule(
            project_id,
            "github",
            config.refresh_interval_days,
            session,
            duration_seconds=time.monotonic() - project_started_at,
            issues_collected=issues_collected,
        )
        await _upsert_collection_watermark(session, project_id, "github")
        logger.info(
            "Completed GitHub data for %s in %s",
            project_name,
            _format_duration(time.monotonic() - project_started_at),
        )
    except Exception as exc:
        logger.exception("Failed to collect GitHub data for %s", project_name)
        error_summary = _summarize_exception(exc)
        stats.errors.append({"project": project_name, "error": error_summary})
        async with session_factory() as err_session:
            await record_refresh_error(project_id, "github", error_summary, err_session)


async def _collect_github(
    settings: Settings,
    config: object,
    session_factory: object,
    limit: int = 0,
    projects: list[str] | None = None,
    run_started_at: float | None = None,
    full_refresh: bool = False,
    force_schedule: bool = False,
    collection_run_id: int | None = None,
    mode: str = "open",
) -> CollectionStats:
    """Run GitHub data collection for all projects.

    Args:
        settings: Application settings.
        config: Dashboard configuration.
        session_factory: Async session factory.
        limit: Max issues per repository (0 = all).
        projects: Project names to collect; None means all configured.
        run_started_at: Monotonic start time for elapsed logging.
        full_refresh: If True, ignore watermarks for closed-issue pass.
        force_schedule: If True, ignore per-project schedule for closed pass.
        collection_run_id: ID of the active collection run.
        mode: Collection mode — "open" collects open issues for every project
            on every run (no schedule gate); "full" collects all issues
            (open + closed) per the per-project refresh schedule; "all"
            forces a full collection for every project regardless of schedule.

    """
    collector = GitHubCollector(
        token=settings.github_token,
        org="canonical",
        maintainers=config.maintainers,
    )
    stats = CollectionStats()
    bots = set(getattr(config, "bots", []))
    _check_initial_rate_limit(collector)

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

            await _collect_project_dependencies(
                settings, config, project_name, project_id, session
            )
            await _collect_project_releases(
                collector, project_name, project_id, session
            )

            # Phase A — open issues, in every mode but "full". "open" stops
            # here; "all" continues into the full pass below.
            if mode != "full":
                await _collect_open_issues(
                    collector,
                    config,
                    session,
                    session_factory,
                    project_name,
                    project_id,
                    stats,
                    bots,
                    limit,
                    collection_run_id,
                )
                if mode == "open":
                    logger.info(
                        "Completed GitHub open-issue collection for %s in %s",
                        project_name,
                        _format_duration(time.monotonic() - project_started_at),
                    )
                    continue

            # Phase B — open plus closed, gated by the refresh schedule in
            # "full" mode and forced in "all" mode.
            if not await _is_full_pass_due(
                session,
                project_name,
                project_id,
                project_started_at,
                mode,
                force_schedule,
            ):
                continue

            await _collect_all_issues(
                collector,
                settings,
                config,
                session,
                session_factory,
                project_name,
                project_id,
                project_started_at,
                stats,
                bots,
                limit,
                collection_run_id,
                full_refresh,
            )

            await _log_rate_limit_after_project(collector, project_name)

            # Avoid GitHub secondary rate limits between repos
            await asyncio.sleep(1)

    return stats
