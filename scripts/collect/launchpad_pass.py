"""Launchpad collection pass."""

import logging
import time

from craft_dashboard.collectors.launchpad import LaunchpadCollector
from craft_dashboard.collectors.scheduler import (
    record_refresh_error,
    update_refresh_schedule,
)
from craft_dashboard.collectors.snapshots import generate_snapshot

from scripts.collect.projects import (
    _get_or_create_project,
    _upsert_collection_watermark,
)
from scripts.collect.reporting import (
    CollectionStats,
    _format_duration,
    _summarize_exception,
)

logger = logging.getLogger(__name__)


async def _collect_launchpad(
    config: object,
    session_factory: object,
    projects: list[str] | None = None,
    run_started_at: float | None = None,
    collection_run_id: int | None = None,
    full_refresh: bool = False,
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
            try:
                bugs_started_at = time.monotonic()
                bug_count = await collector.collect_bugs(
                    lp_name,
                    project_id,
                    session,
                    collection_run_id=collection_run_id,
                    full_refresh=full_refresh,
                )
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
                    filtered_issue_ids=set(config.filtered_issues.get(lp_name, []))
                    or None,
                )
                await _upsert_collection_watermark(session, project_id, "launchpad")
                logger.info(
                    "  Generated snapshot for %s in %s",
                    lp_project_name,
                    _format_duration(time.monotonic() - snapshot_started_at),
                )

                # Record last_refreshed_at so the hourly rotation (see
                # `get_least_recently_refreshed`) can consider Launchpad
                # projects alongside GitHub ones. Launchpad has no open/full
                # split or schedule gate of its own — scheduled runs are
                # incremental from the watermark, and rotation passes
                # `full_refresh=True` — so this is purely bookkeeping for
                # rotation ordering, not a gate on this path.
                await update_refresh_schedule(
                    project_id,
                    "launchpad",
                    config.refresh_interval_days,
                    session,
                    duration_seconds=time.monotonic() - bugs_started_at,
                    issues_collected=bug_count,
                )
            except Exception as exc:
                logger.exception("Failed to collect Launchpad data for %s", lp_name)
                stats.errors.append(
                    {"project": lp_name, "error": _summarize_exception(exc)}
                )
                error_summary = _summarize_exception(exc)
                async with session_factory() as err_session:
                    await record_refresh_error(
                        project_id, "launchpad", error_summary, err_session
                    )

    return stats
