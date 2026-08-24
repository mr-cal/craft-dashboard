"""Discourse forum activity collector (see plans/33-forum-activity-tracker.md).

Tracks every category on each configured forum (no per-forum category
scoping — see the storage feasibility analysis in the plan). Two entry
points are used by the scheduling logic in ``scripts/collect_forum_data.py``:

- ``backfill_month``: pulls one calendar month of topics via Discourse's
  ``search.json`` endpoint. Used both for historical backfill (one month
  per scheduled run, walking backward) and for refreshing recent months.
- ``refresh_recent``: re-runs ``backfill_month`` for the current month (and
  the previous month, if it "ended after the last refresh") so topics that
  got new replies have their ``posts_count``/``last_posted_at`` updated.

Also caches each forum's category list (``refresh_categories``), which
backs the per-category checkbox filter on the Engagement page.
"""

from __future__ import annotations

import calendar
import logging
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from craft_dashboard.models.forum import ForumBackfillState, ForumTopic

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from craft_dashboard.config import ForumConfig

__all__ = ["ForumCollector"]

logger = logging.getLogger(__name__)

HTTP_TOO_MANY_REQUESTS = 429
#: Discourse's search.json returns results in pages of up to this many
#: topics; used as a loop-termination signal (a short page means we've
#: reached the end of this month's results).
_SEARCH_PAGE_SIZE = 50
#: Hard cap on pages fetched per month, as a defensive backstop against an
#: unexpectedly large month or the search API's known relevance-ordering
#: quirks (it isn't strictly paginated the way an offset/limit API would be).
_MAX_PAGES_PER_MONTH = 60
#: Default historical backfill lookback, matching the reference gist's
#: ``YEARS = 7``.
DEFAULT_YEARS_LOOKBACK = 7
_ONE_DAY = timedelta(days=1)


def _is_retriable(exc: BaseException) -> bool:
    """Return True for transient errors that should trigger a retry.

    Retries on 429 (Discourse rate limiting) and network/timeout/protocol
    errors. Does not retry on other 4xx errors.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == HTTP_TOO_MANY_REQUESTS
    return isinstance(exc, httpx.TransportError)


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Extract a Retry-After header value (seconds) from a 429 response."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    retry_after = exc.response.headers.get("Retry-After")
    if not retry_after:
        return None
    try:
        return float(retry_after)
    except ValueError:
        return None


def _make_wait(default_wait: Callable) -> Callable[[RetryCallState], float]:
    """Build a tenacity ``wait`` callable that honors Discourse's Retry-After.

    Falls back to exponential backoff when no Retry-After header is present
    (e.g. transport errors, or a 429 that omitted it).
    """

    def _wait(retry_state: RetryCallState) -> float:
        exception = retry_state.outcome.exception() if retry_state.outcome else None
        if exception is not None:
            retry_after = _retry_after_seconds(exception)
            if retry_after is not None:
                return retry_after
        return default_wait(retry_state)

    return _wait


def _before_sleep_log(retry_state: RetryCallState) -> None:
    """Log each retry at debug level so progress logs stay uncluttered."""
    exception = retry_state.outcome.exception() if retry_state.outcome else None
    if exception is None:
        return
    logger.debug(
        "Forum HTTP retry (attempt %d): %s",
        retry_state.attempt_number,
        exception,
    )


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    """Return the (inclusive start, exclusive end) dates for a calendar month."""
    start = date(year, month, 1)
    days_in_month = calendar.monthrange(year, month)[1]
    end = date(year, month, days_in_month) + _ONE_DAY
    return start, end


def _add_months(d: date, delta: int) -> date:
    """Return the first day of the month ``delta`` months from ``d``."""
    month_index = d.month - 1 + delta
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _months_between(earlier: date, later: date) -> int:
    """Return the whole number of months between two first-of-month dates."""
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def _flatten_categories(categories_payload: dict) -> list[dict]:
    """Flatten a /categories.json response into a list of {id, slug} dicts.

    Includes subcategories (requested via include_subcategories=true), so
    every category on the forum is represented, not just top-level ones.
    """
    flattened: list[dict] = []
    for cat in categories_payload.get("category_list", {}).get("categories", []):
        flattened.append({"id": cat["id"], "slug": cat["slug"]})
        flattened.extend(
            {"id": sub["id"], "slug": sub["slug"]}
            for sub in cat.get("subcategory_list") or []
        )
    return flattened


class ForumCollector:
    """Collects topic-level activity data from configured Discourse forums."""

    def __init__(
        self,
        forums: dict[str, ForumConfig],
        years_lookback: int = DEFAULT_YEARS_LOOKBACK,
    ) -> None:
        """Initialize the forum collector.

        Args:
            forums: Mapping of forum key (e.g. "snapcraft") to its config.
            years_lookback: How many years back historical backfill should go.

        """
        self.forums = forums
        self.years_lookback = years_lookback
        self._http: httpx.AsyncClient | None = None
        # Per-run cache of category id -> slug, keyed by forum. Refetched
        # once per collector instance (not per month) to avoid redundant
        # /categories.json calls during a multi-month backfill run.
        self._category_map_cache: dict[str, dict[int, str]] = {}

    @property
    def http(self) -> httpx.AsyncClient:
        """Return a persistent HTTP client, creating one if needed."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    @retry(
        retry=retry_if_exception(_is_retriable),
        wait=_make_wait(wait_exponential(multiplier=2, min=4, max=120)),
        stop=stop_after_attempt(6),
        before_sleep=_before_sleep_log,
        reraise=True,
    )
    async def _get_json(self, url: str, params: dict | None = None) -> dict:
        """GET a Discourse JSON endpoint with 429/backoff handling."""
        response = await self.http.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def _get_category_map(self, forum: str, base_url: str) -> dict[int, str]:
        """Return (and cache) a category-id -> slug mapping for a forum."""
        if forum not in self._category_map_cache:
            payload = await self._get_json(
                f"{base_url}/categories.json",
                params={"include_subcategories": "true"},
            )
            categories = _flatten_categories(payload)
            self._category_map_cache[forum] = {c["id"]: c["slug"] for c in categories}
        return self._category_map_cache[forum]

    async def _get_backfill_state(
        self, forum: str, session: AsyncSession
    ) -> ForumBackfillState:
        """Get or create the ForumBackfillState row for a forum."""
        state = await session.scalar(
            select(ForumBackfillState).where(ForumBackfillState.forum == forum)
        )
        if state is None:
            state = ForumBackfillState(forum=forum, categories_cache=[])
            session.add(state)
            await session.flush()
        return state

    async def refresh_categories(self, forum: str, session: AsyncSession) -> int:
        """Refresh the cached category list for a forum.

        Returns:
            The number of categories cached.

        """
        config = self.forums[forum]
        categories = _flatten_categories(
            await self._get_json(
                f"{config.base_url}/categories.json",
                params={"include_subcategories": "true"},
            )
        )
        self._category_map_cache[forum] = {c["id"]: c["slug"] for c in categories}
        slugs = sorted({c["slug"] for c in categories})

        state = await self._get_backfill_state(forum, session)
        state.categories_cache = slugs
        state.categories_cached_at = datetime.now(tz=UTC)
        await session.commit()
        logger.info("Forum categories cached: %s — %d categories", forum, len(slugs))
        return len(slugs)

    async def backfill_month(
        self,
        forum: str,
        year: int,
        month: int,
        session: AsyncSession,
    ) -> int:
        """Backfill (or refresh) one calendar month of topics for a forum.

        Walks Discourse's search.json across the whole forum (every
        category), upserting a ForumTopic row per topic found. Safe to call
        repeatedly for the same month — upsert-by-(forum, external_id)
        means re-running it just refreshes posts_count/last_posted_at for
        topics that got new replies.

        Args:
            forum: Forum key from craft-dashboard.toml's [forums.*] sections.
            year: Calendar year of the month to backfill.
            month: Calendar month (1-12) to backfill.
            session: An async SQLAlchemy session.

        Returns:
            The number of distinct topics upserted for this month.

        """
        config = self.forums[forum]
        start, end = _month_bounds(year, month)
        category_map = await self._get_category_map(forum, config.base_url)

        seen_ids: set[int] = set()
        page = 1
        while page <= _MAX_PAGES_PER_MONTH:
            payload = await self._get_json(
                f"{config.base_url}/search.json",
                params={
                    "q": f"after:{start.isoformat()} before:{end.isoformat()}",
                    "page": page,
                },
            )
            topics = payload.get("topics", [])
            new_topics = [t for t in topics if t["id"] not in seen_ids]
            if not new_topics:
                break

            now = datetime.now(tz=UTC)
            for topic in new_topics:
                seen_ids.add(topic["id"])
                created_at = datetime.fromisoformat(
                    topic["created_at"].replace("Z", "+00:00")
                )
                last_posted_at = None
                if topic.get("last_posted_at"):
                    last_posted_at = datetime.fromisoformat(
                        topic["last_posted_at"].replace("Z", "+00:00")
                    )
                category_slug = category_map.get(
                    topic.get("category_id"), str(topic.get("category_id", ""))
                )
                stmt = insert(ForumTopic).values(
                    forum=forum,
                    category=category_slug,
                    external_id=topic["id"],
                    title=topic["title"],
                    posts_count=topic.get("posts_count", 0),
                    like_count=topic.get("like_count", 0),
                    created_at=created_at,
                    last_posted_at=last_posted_at,
                    url=f"{config.base_url}/t/{topic.get('slug', '')}/{topic['id']}",
                    last_fetched_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["forum", "external_id"],
                    set_={
                        "category": stmt.excluded.category,
                        "title": stmt.excluded.title,
                        "posts_count": stmt.excluded.posts_count,
                        "like_count": stmt.excluded.like_count,
                        "last_posted_at": stmt.excluded.last_posted_at,
                        "last_fetched_at": stmt.excluded.last_fetched_at,
                    },
                )
                await session.execute(stmt)

            await session.commit()

            if len(topics) < _SEARCH_PAGE_SIZE:
                break
            page += 1

        logger.debug(
            "Forum backfill: %s %04d-%02d — %d topics (page-level detail)",
            forum,
            year,
            month,
            len(seen_ids),
        )
        return len(seen_ids)

    async def refresh_recent(self, forum: str, session: AsyncSession) -> int:
        """Refresh the current month (and previous month if due) for a forum.

        Per the "re-refresh the current month, or previous month if it ended
        after the last refresh" rule: if the previous month's last day is
        after the last successful refresh, that month is refreshed too (it
        may have gotten late updates after the last refresh already ran).

        Args:
            forum: Forum key from craft-dashboard.toml's [forums.*] sections.
            session: An async SQLAlchemy session.

        Returns:
            Total topics upserted across the refreshed month(s).

        """
        state = await self._get_backfill_state(forum, session)
        now = datetime.now(tz=UTC)
        today = now.date()

        total = await self.backfill_month(forum, today.year, today.month, session)

        previous_month_start = _add_months(date(today.year, today.month, 1), -1)
        _, previous_month_end = _month_bounds(
            previous_month_start.year, previous_month_start.month
        )
        last_refresh = state.last_incremental_refresh_at
        previous_month_ended_after_last_refresh = (
            last_refresh is None or last_refresh.date() < previous_month_end
        )
        if previous_month_ended_after_last_refresh:
            total += await self.backfill_month(
                forum,
                previous_month_start.year,
                previous_month_start.month,
                session,
            )

        state.last_incremental_refresh_at = now
        await session.commit()
        logger.info(
            "Forum refresh: %s (current%s month) — %d topics updated",
            forum,
            " + previous" if previous_month_ended_after_last_refresh else "",
            total,
        )
        return total

    async def backfill_next_month(self, forum: str, session: AsyncSession) -> int:
        """Backfill the next not-yet-covered month, going backward in time.

        Intended to be called once per scheduled run (e.g. daily) so a full
        historical backfill spreads out across many days instead of
        blocking in one long run. Self-healing: always resumes from
        ``earliest_month_backfilled`` rather than a fixed offset, so a
        missed run just means the next run picks up where it left off.

        Returns:
            The number of topics upserted for the month backfilled, or 0 if
            the configured lookback window has already been fully covered.

        """
        state = await self._get_backfill_state(forum, session)
        today = datetime.now(tz=UTC).date()
        oldest_target = _add_months(
            date(today.year, today.month, 1), -12 * self.years_lookback
        )

        if state.earliest_month_backfilled is None:
            target_month = date(today.year, today.month, 1)
        else:
            earliest = state.earliest_month_backfilled.date()
            if earliest <= oldest_target:
                logger.info(
                    "Forum backfill: %s — fully backfilled to %s, nothing to do",
                    forum,
                    oldest_target.isoformat(),
                )
                return 0
            target_month = _add_months(earliest, -1)

        months_remaining = _months_between(oldest_target, target_month)
        count = await self.backfill_month(
            forum, target_month.year, target_month.month, session
        )

        state.earliest_month_backfilled = datetime(
            target_month.year, target_month.month, 1, tzinfo=UTC
        )
        await session.commit()

        logger.info(
            "Forum backfill: %s %04d-%02d — %d topics "
            "(running total covers back to %04d-%02d, ~%d months remaining)",
            forum,
            target_month.year,
            target_month.month,
            count,
            target_month.year,
            target_month.month,
            months_remaining,
        )
        return count
