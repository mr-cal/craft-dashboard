#!/usr/bin/env python3
"""Discourse forum activity collection entry point for cron jobs.

Two complementary jobs keep forum data current:

* **backfill** — walks one not-yet-covered month backward per run, per
  forum, until ``years-lookback`` (default 7 years) of history is covered.
  Intended to run frequently (e.g. every 15 minutes) so the initial
  historical backfill completes in a reasonable number of days without
  ever issuing a long-running blocking request.
* **refresh** — re-fetches the current month (and the previous month, if it
  ended after the last refresh) for every forum whose
  ``last_incremental_refresh_at`` is more than ``--refresh-interval-days``
  (default 5) old. Self-healing: a missed run just means the next run sees
  an older timestamp and still catches up, with no separate catch-up logic.

Usage:
    uv run scripts/collect_forum_data.py --mode backfill
    uv run scripts/collect_forum_data.py --mode refresh
    uv run scripts/collect_forum_data.py --mode all
    uv run scripts/collect_forum_data.py --mode backfill --forum snapcraft -v

Environment variables:
    DATABASE_URL: PostgreSQL connection URL
"""

import asyncio
import logging
import pathlib
import sys
import time
from datetime import UTC, datetime, timedelta

import click
from sqlalchemy import select

# Add project root to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from craft_dashboard.collectors.forum import ForumCollector
from craft_dashboard.config import load_config
from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.models.forum import ForumBackfillState
from craft_dashboard.settings import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_DEFAULT_REFRESH_INTERVAL_DAYS = 5


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


async def _run_backfill(
    collector: ForumCollector,
    session_factory,
    forums: list[str],
) -> int:
    """Backfill one not-yet-covered month per forum. Returns topics upserted."""
    total = 0
    for forum in forums:
        async with session_factory() as session:
            try:
                total += await collector.backfill_next_month(forum, session)
            except Exception:
                logger.exception("Backfill failed for forum %r", forum)
    return total


async def _run_refresh(
    collector: ForumCollector,
    session_factory,
    forums: list[str],
    refresh_interval_days: int,
) -> int:
    """Refresh recent months for forums overdue by refresh_interval_days."""
    total = 0
    cutoff = datetime.now(tz=UTC) - timedelta(days=refresh_interval_days)
    for forum in forums:
        async with session_factory() as session:
            state = await session.scalar(
                select(ForumBackfillState).where(ForumBackfillState.forum == forum)
            )
            last_refresh = state.last_incremental_refresh_at if state else None
            # SQLite (used in tests) doesn't round-trip tzinfo on
            # DateTime(timezone=True) columns, so normalize to UTC before
            # comparing rather than assuming the driver preserved it.
            if last_refresh is not None and last_refresh.tzinfo is None:
                last_refresh = last_refresh.replace(tzinfo=UTC)
            if last_refresh is not None and last_refresh > cutoff:
                logger.debug(
                    "Skipping refresh for %s: last refreshed %s (within %d days)",
                    forum,
                    last_refresh,
                    refresh_interval_days,
                )
                continue
            try:
                total += await collector.refresh_recent(forum, session)
            except Exception:
                logger.exception("Refresh failed for forum %r", forum)
    return total


async def _main(
    mode: str,
    forums_filter: list[str],
    verbose: bool,
    refresh_interval_days: int,
    years_lookback: int,
) -> None:
    """Run forum data collection."""
    settings = Settings()

    log_level = (
        logging.DEBUG
        if verbose
        else getattr(logging, settings.log_level.upper(), logging.INFO)
    )
    logging.getLogger().setLevel(log_level)
    # httpx logs every request at INFO by default, which drowns out our own
    # tasteful per-month progress lines; keep it at WARNING unless -v is set.
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)

    config = load_config(pathlib.Path(settings.config_file))
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)

    forums = dict(config.forums)
    if forums_filter:
        forums = {name: cfg for name, cfg in forums.items() if name in forums_filter}
        missing = set(forums_filter) - set(forums)
        if missing:
            logger.warning("Unknown forum(s) requested, skipping: %s", sorted(missing))
    if not forums:
        logger.warning("No forums configured; nothing to do")
        await engine.dispose()
        return

    forum_names = sorted(forums)
    logger.info("Forum collection starting: mode=%s forums=%s", mode, forum_names)

    collector = ForumCollector(forums, years_lookback=years_lookback)
    run_started_at = time.monotonic()
    try:
        # The category cache is cheap and used by the Engagement page's
        # checkboxes, so refresh it on every run regardless of mode.
        for forum in forum_names:
            async with session_factory() as session:
                try:
                    await collector.refresh_categories(forum, session)
                except Exception:
                    logger.exception("Failed to refresh categories for forum %r", forum)

        topics_upserted = 0
        if mode in ("backfill", "all"):
            topics_upserted += await _run_backfill(
                collector, session_factory, forum_names
            )
        if mode in ("refresh", "all"):
            topics_upserted += await _run_refresh(
                collector, session_factory, forum_names, refresh_interval_days
            )

        logger.info(
            "Forum collection complete: mode=%s forums=%d topics_upserted=%d total_time=%s",
            mode,
            len(forum_names),
            topics_upserted,
            _format_duration(time.monotonic() - run_started_at),
        )
    finally:
        await collector.close()
        await engine.dispose()


@click.command()
@click.option(
    "--mode",
    type=click.Choice(["backfill", "refresh", "all"]),
    default="all",
    help=(
        "'backfill' walks one not-yet-covered historical month backward per "
        "forum; 'refresh' re-fetches recent months for forums overdue per "
        "--refresh-interval-days; 'all' runs both."
    ),
)
@click.option(
    "--forum",
    "forums",
    multiple=True,
    help="Only collect data for these forums (repeatable). Default: all configured forums.",
)
@click.option(
    "--refresh-interval-days",
    default=_DEFAULT_REFRESH_INTERVAL_DAYS,
    type=int,
    help="Re-refresh recent months for a forum only if this many days have passed since its last refresh.",
)
@click.option(
    "--years-lookback",
    default=7,
    type=int,
    help="How many years of history to backfill before stopping.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable debug logging (per-page fetches, retry/backoff details). Overrides LOG_LEVEL.",
)
def main(
    mode: str,
    forums: tuple[str, ...],
    refresh_interval_days: int,
    years_lookback: int,
    verbose: bool,
) -> None:
    """Collect Discourse forum activity data."""
    asyncio.run(
        _main(
            mode,
            list(forums),
            verbose,
            refresh_interval_days=refresh_interval_days,
            years_lookback=years_lookback,
        )
    )


if __name__ == "__main__":
    main()
