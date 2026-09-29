#!/usr/bin/env python3
"""Data collection entry point for cron jobs.

Usage:
    uv run scripts/collect_data.py --source all
    uv run scripts/collect_data.py --source github
    uv run scripts/collect_data.py --source launchpad
    uv run scripts/collect_data.py --source github --limit 25
    uv run scripts/collect_data.py --source github --project snapcraft --project rockcraft
    uv run scripts/collect_data.py --source github --limit 25 --project snapcraft
    uv run scripts/collect_data.py --mode rotation

Environment variables:
    DATABASE_URL: PostgreSQL connection URL
    GITHUB_TOKEN: GitHub personal access token
"""

import asyncio
import logging
import pathlib
import sys
import time

import click

# Add project root to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from craft_dashboard.collectors.scheduler import get_least_recently_refreshed
from craft_dashboard.collectors.snapshots import generate_cross_project_snapshot
from craft_dashboard.config import load_config
from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.repositories.issue_link_repository import IssueLinkRepository
from craft_dashboard.settings import Settings

from scripts.collect.github_pass import _collect_github
from scripts.collect.launchpad_pass import _collect_launchpad
from scripts.collect.reporting import (
    CollectionStats,
    _format_duration,
    _summarize_exception,
)
from scripts.collect.runs import (
    _create_collection_run,
    _finish_collection_run,
    _wait_for_source_available,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _main(
    source: str,
    limit: int,
    projects: list[str],
    verbose: bool,
    full_refresh: bool = False,
    force_schedule: bool = False,
    mode: str = "open",
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
    if force_schedule:
        logger.info(
            "Force mode: ignoring refresh schedule, collecting all projects now"
        )
    elif full_refresh:
        logger.info("Full collection mode enabled; ignoring saved watermarks")
    else:
        logger.info("Incremental collection mode enabled; using saved watermarks")
    logger.info("Collection mode: %s", mode)

    try:
        run_started_at = time.monotonic()
        stats = CollectionStats()

        async def _run_source(
            source_name: str,
            make_collector_call: object,
        ) -> CollectionStats:
            run = await _create_collection_run(source_name, session_factory)
            try:
                source_stats = await make_collector_call(run.id)
            except Exception as exc:
                await _finish_collection_run(
                    run,
                    session_factory,
                    status="failed",
                    projects_processed=0,
                    issues_collected=0,
                    errors=[
                        {"source": source_name, "error": _summarize_exception(exc)}
                    ],
                )
                raise

            await _finish_collection_run(
                run,
                session_factory,
                status="completed",
                projects_processed=len(source_stats.projects_processed),
                issues_collected=source_stats.issues_collected,
                errors=source_stats.errors,
            )
            return source_stats

        if mode == "rotation":
            # Continuous rotation: pick exactly one project+source pair
            # (across both GitHub and Launchpad) — whichever has gone
            # longest without a full refresh — and fully refresh just that
            # one. Replaces the old weekly-distributed schedule: run this
            # hourly and every project eventually gets refreshed as the
            # rotation cycles through the full list. This mode ignores
            # --source/--project; the rotation always chooses its own
            # target.
            async with session_factory() as session:
                target = await get_least_recently_refreshed(session)

            if target is None:
                logger.warning("Rotation: no projects found; nothing to refresh")
            else:
                _project_id, project_name, target_source = target
                existing_running = await _wait_for_source_available(
                    session_factory, target_source
                )
                if existing_running is not None:
                    logger.warning(
                        "Collection run %s already in progress for %s (started %s); "
                        "skipping this invocation",
                        existing_running.id,
                        target_source,
                        existing_running.started_at,
                    )
                    raise SystemExit(1)

                logger.info(
                    "Rotation: selected %s (%s) for full refresh",
                    project_name,
                    target_source,
                )
                if target_source == "launchpad":
                    lp_name = project_name.removesuffix(" (launchpad)")
                    stats.merge(
                        await _run_source(
                            "launchpad",
                            lambda run_id, _lp=lp_name: _collect_launchpad(
                                config,
                                session_factory,
                                projects=[_lp],
                                run_started_at=run_started_at,
                                collection_run_id=run_id,
                                # Rotation exists to fully refresh one project
                                # at a time, so it must ignore the watermark.
                                # This is also what lets the system heal gaps
                                # on its own instead of needing a manual
                                # re-collection.
                                full_refresh=True,
                            ),
                        )
                    )
                else:
                    stats.merge(
                        await _run_source(
                            "github",
                            lambda run_id, _pn=project_name: _collect_github(
                                settings,
                                config,
                                session_factory,
                                limit=limit,
                                projects=[_pn],
                                run_started_at=run_started_at,
                                force_schedule=True,
                                collection_run_id=run_id,
                                mode="all",
                            ),
                        )
                    )
        else:
            sources_to_check: list[str] = []
            if source in ("all", "github"):
                sources_to_check.append("github")
            if source in ("all", "launchpad"):
                sources_to_check.append("launchpad")

            # NOTE: this checks all requested sources up front and aborts the
            # entire invocation (SystemExit) if a conflict is still present after
            # waiting (see _wait_for_source_available), rather than skipping only
            # the conflicting source and still running the others. With --source
            # all, a stuck/long-running launchpad run would
            # currently also block an otherwise-healthy github collection in the
            # same invocation. This is a known, low-urgency gap: production cron
            # always passes an explicit single --source (github or launchpad), so
            # it isn't hit in practice today, but a future --source all cron
            # invocation would need this loosened to per-source skipping instead
            # of an all-or-nothing abort.
            for source_name in sources_to_check:
                existing_running = await _wait_for_source_available(
                    session_factory,
                    source_name,
                )
                if existing_running is not None:
                    logger.warning(
                        "Collection run %s already in progress for %s (started %s); skipping this invocation",
                        existing_running.id,
                        source_name,
                        existing_running.started_at,
                    )
                    raise SystemExit(1)

            if source in ("all", "github"):
                stats.merge(
                    await _run_source(
                        "github",
                        lambda run_id: _collect_github(
                            settings,
                            config,
                            session_factory,
                            limit=limit,
                            projects=project_filter,
                            run_started_at=run_started_at,
                            full_refresh=full_refresh,
                            force_schedule=force_schedule,
                            collection_run_id=run_id,
                            mode=mode,
                        ),
                    )
                )
            if source in ("all", "launchpad"):
                stats.merge(
                    await _run_source(
                        "launchpad",
                        lambda run_id: _collect_launchpad(
                            config,
                            session_factory,
                            projects=project_filter,
                            run_started_at=run_started_at,
                            collection_run_id=run_id,
                            full_refresh=full_refresh,
                        ),
                    )
                )
        logger.info(
            "Collection complete: %d projects processed, %d issues collected, total time: %s",
            len(stats.projects_processed),
            stats.issues_collected,
            _format_duration(time.monotonic() - run_started_at),
        )

        # Generate cross-project aggregate snapshot with true medians
        try:
            cross_started_at = time.monotonic()
            bots = set(getattr(config, "bots", []))
            async with session_factory() as session:
                await generate_cross_project_snapshot(
                    session,
                    set(config.maintainers),
                    bots=bots,
                    filtered_issues=config.filtered_issues or None,
                )
            logger.info(
                "Cross-project snapshot generated in %s",
                _format_duration(time.monotonic() - cross_started_at),
            )
        except Exception:  # noqa: BLE001 - a snapshot failure must not discard collected data
            logger.warning(
                "Failed to generate cross-project snapshot",
                exc_info=True,
            )

        # Reconcile previously unresolved issue links against any newly collected issues
        try:
            async with session_factory() as session:
                link_repo = IssueLinkRepository(session)
                reconciled = await link_repo.reconcile_unresolved_links()
                if reconciled:
                    await session.commit()
                    logger.info(
                        "Reconciled %d previously unresolved issue link(s)",
                        reconciled,
                    )
        except Exception:  # noqa: BLE001 - reconciliation is best-effort and retried next run
            logger.warning(
                "Failed to reconcile unresolved issue links",
                exc_info=True,
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
@click.option(
    "--full-refresh",
    is_flag=True,
    default=False,
    help="Ignore saved watermarks and run full collection where refresh is due.",
)
@click.option(
    "--force-schedule",
    is_flag=True,
    default=False,
    help="Ignore the refresh schedule and collect all projects now, regardless of when they were last collected.",
)
@click.option(
    "--mode",
    type=click.Choice(["open", "full", "all", "rotation"]),
    default="open",
    help=(
        "Collection mode: 'open' (default) refreshes open issues for every project on "
        "every run with no schedule gate; 'full' refreshes all issues (open + closed) "
        "per the per-project schedule; 'all' forces a full collection for every project "
        "regardless of schedule; 'rotation' picks exactly one project (GitHub or "
        "Launchpad, whichever has gone longest without a full refresh) and fully "
        "refreshes just that one — intended to run hourly, cycling through every "
        "project over time. Ignores --source/--project."
    ),
)
def main(
    source: str,
    limit: int,
    projects: tuple[str, ...],
    verbose: bool,
    full_refresh: bool,
    force_schedule: bool,
    mode: str,
) -> None:
    """Collect data from external sources."""
    asyncio.run(
        _main(
            source,
            limit,
            list(projects),
            verbose,
            full_refresh=full_refresh,
            force_schedule=force_schedule,
            mode=mode,
        )
    )


if __name__ == "__main__":
    main()
