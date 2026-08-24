"""Discourse forum activity collector (see plans/33-forum-activity-tracker.md).

Tracks every category on each configured forum (no per-forum category
scoping — see the storage feasibility analysis in the plan). Two entry
points are used by the scheduling logic in ``scripts/collect_forum_data.py``:

- ``backfill_next_batch``: walks each category's topic list one page at a
  time (see "Why category listings, not search.json" below), upserting
  topics and advancing a resumable per-category page cursor. Bounded by
  ``max_requests`` per call so a single scheduled run never blocks for too
  long; self-healing across runs since progress is persisted.
- ``refresh_recent``: re-scans the first page(s) of every category (newest
  topics first) to catch ``posts_count``/``last_posted_at`` updates on
  topics created in the current or previous month.

Also caches each forum's category list (``refresh_categories``), which
backs the per-category checkbox filter on the Engagement page.

Why category listings, not search.json
---------------------------------------
An earlier version of this collector used Discourse's ``/search.json``
endpoint with an ``after:``/``before:`` date-range query, paginating until
a page came back empty. That pagination is **not reliable**: Discourse's
search index can return an empty page in the middle of a real result set
(confirmed against forum.snapcraft.io — page 3 of a query returned zero
topics while pages 4-5 returned dozens more, none of which overlapped with
pages 1-2). Stopping at the first empty page silently dropped topics,
which is why some categories showed suspiciously sparse or all-zero months.

Discourse's per-category topic list (``GET /c/{slug}/{id}.json?order=created``)
paginates properly instead: each page's ``more_topics_url`` is only null
once every topic in the category has been returned, and topics are
strictly ordered by creation date, so backfill can also stop early once it
sees a topic older than any configured lookback cutoff.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
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
#: Default historical backfill lookback. Set generously high (these forums
#: were all created well within the last 15 years) so a fresh backfill
#: effectively collects "all" history rather than a rolling window — see
#: the storage-feasibility analysis in plans/33-forum-activity-tracker.md
#: for why keeping full history is cheap enough to just do.
DEFAULT_YEARS_LOOKBACK = 15
#: Safety cap on category-listing pages fetched in a single
#: ``backfill_next_batch`` call, so one scheduled run can't block
#: indefinitely; progress is resumed on the next call via the persisted
#: per-category page cursor.
DEFAULT_MAX_REQUESTS_PER_BATCH = 300


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


def _add_months(d: date, delta: int) -> date:
    """Return the first day of the month ``delta`` months from ``d``."""
    month_index = d.month - 1 + delta
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _parse_discourse_datetime(value: str) -> datetime:
    """Parse a Discourse ISO-8601 timestamp (Z suffix) into an aware datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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
        # once per collector instance (not per batch) to avoid redundant
        # /categories.json calls during a multi-batch backfill run.
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

    async def _get_category_topics_page(
        self, base_url: str, slug: str, category_id: int, page: int
    ) -> tuple[list[dict], str | None]:
        """Fetch one page of a category's topic list, newest-created first.

        Returns:
            (topics, more_topics_url) — more_topics_url is None once the
            last page of the category has been reached.

        """
        payload = await self._get_json(
            f"{base_url}/c/{slug}/{category_id}.json",
            params={"order": "created", "page": page},
        )
        topic_list = payload.get("topic_list", {})
        return topic_list.get("topics", []), topic_list.get("more_topics_url")

    async def _get_backfill_state(
        self, forum: str, session: AsyncSession
    ) -> ForumBackfillState:
        """Get or create the ForumBackfillState row for a forum."""
        state = await session.scalar(
            select(ForumBackfillState).where(ForumBackfillState.forum == forum)
        )
        if state is None:
            state = ForumBackfillState(
                forum=forum, categories_cache=[], category_progress={}
            )
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

    async def _upsert_topics(
        self,
        forum: str,
        category_slug: str,
        base_url: str,
        topics: list[dict],
        session: AsyncSession,
    ) -> int:
        """Upsert a page of topics for one category. Returns count upserted."""
        now = datetime.now(tz=UTC)
        for topic in topics:
            created_at = _parse_discourse_datetime(topic["created_at"])
            last_posted_at = None
            if topic.get("last_posted_at"):
                last_posted_at = _parse_discourse_datetime(topic["last_posted_at"])
            stmt = insert(ForumTopic).values(
                forum=forum,
                category=category_slug,
                external_id=topic["id"],
                title=topic["title"],
                posts_count=topic.get("posts_count", 0),
                like_count=topic.get("like_count", 0),
                created_at=created_at,
                last_posted_at=last_posted_at,
                url=f"{base_url}/t/{topic.get('slug', '')}/{topic['id']}",
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
        return len(topics)

    async def backfill_next_batch(
        self,
        forum: str,
        session: AsyncSession,
        *,
        max_requests: int = DEFAULT_MAX_REQUESTS_PER_BATCH,
    ) -> int:
        """Advance historical backfill by up to ``max_requests`` category pages.

        Walks every not-yet-fully-backfilled category, fetching its next
        not-yet-fetched page (cursor resumed from ``category_progress``),
        upserting topics, and marking a category "done" once its
        ``more_topics_url`` is null or a topic older than the configured
        years-lookback cutoff is seen. Safe to call repeatedly (e.g. once
        per scheduled run) until every category is done, at which point
        it's a fast no-op.

        Args:
            forum: Forum key from craft-dashboard.toml's [forums.*] sections.
            session: An async SQLAlchemy session.
            max_requests: Upper bound on category-listing pages fetched in
                this call.

        Returns:
            The number of topics upserted in this batch.

        """
        config = self.forums[forum]
        state = await self._get_backfill_state(forum, session)
        category_map = await self._get_category_map(forum, config.base_url)

        today = datetime.now(tz=UTC).date()
        oldest_target = _add_months(
            date(today.year, today.month, 1), -12 * self.years_lookback
        )

        progress: dict[str, dict] = dict(state.category_progress or {})
        topics_upserted = 0
        requests_made = 0

        for category_id, slug in category_map.items():
            if requests_made >= max_requests:
                break
            cat_key = str(category_id)
            cat_progress = dict(progress.get(cat_key, {"next_page": 0, "done": False}))
            if cat_progress.get("done"):
                continue

            page = cat_progress["next_page"]
            topics, more_url = await self._get_category_topics_page(
                config.base_url, slug, category_id, page
            )
            requests_made += 1

            if not topics:
                cat_progress["done"] = True
                progress[cat_key] = cat_progress
                continue

            topics_upserted += await self._upsert_topics(
                forum, slug, config.base_url, topics, session
            )
            oldest_seen = min(
                _parse_discourse_datetime(t["created_at"]) for t in topics
            )
            reached_cutoff = oldest_seen.date() < oldest_target
            if more_url is None or reached_cutoff:
                cat_progress["done"] = True
            else:
                cat_progress["next_page"] = page + 1
            progress[cat_key] = cat_progress

        state.category_progress = progress
        await session.commit()

        done_count = sum(1 for v in progress.values() if v.get("done"))
        total_count = len(category_map)
        logger.info(
            "Forum backfill: %s — %d/%d categories complete, "
            "%d topics upserted this batch (%d page requests)",
            forum,
            done_count,
            total_count,
            topics_upserted,
            requests_made,
        )
        return topics_upserted

    async def refresh_recent(self, forum: str, session: AsyncSession) -> int:
        """Re-scan the newest topics in every category to catch recent activity.

        For each category, re-fetches page(s) of its newest-first topic
        list (independent of the backfill cursor) until a topic older than
        the previous calendar month is seen, upserting each page. This
        refreshes ``posts_count``/``last_posted_at`` for topics created in
        the current or previous month — matching the original "current +
        previous month" refresh scope (it doesn't catch new replies on
        older topics, which is an accepted tradeoff for topic-level
        aggregate tracking; see plans/33-forum-activity-tracker.md).

        Args:
            forum: Forum key from craft-dashboard.toml's [forums.*] sections.
            session: An async SQLAlchemy session.

        Returns:
            Total topics upserted across all categories.

        """
        config = self.forums[forum]
        state = await self._get_backfill_state(forum, session)
        now = datetime.now(tz=UTC)
        today = now.date()
        previous_month_start = _add_months(date(today.year, today.month, 1), -1)
        category_map = await self._get_category_map(forum, config.base_url)

        total = 0
        for category_id, slug in category_map.items():
            page = 0
            while True:
                topics, more_url = await self._get_category_topics_page(
                    config.base_url, slug, category_id, page
                )
                if not topics:
                    break
                total += await self._upsert_topics(
                    forum, slug, config.base_url, topics, session
                )
                oldest_seen = min(
                    _parse_discourse_datetime(t["created_at"]) for t in topics
                )
                if more_url is None or oldest_seen.date() < previous_month_start:
                    break
                page += 1

        state.last_incremental_refresh_at = now
        await session.commit()
        logger.info(
            "Forum refresh: %s — %d topics updated across %d categories",
            forum,
            total,
            len(category_map),
        )
        return total
