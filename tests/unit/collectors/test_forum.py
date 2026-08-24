"""Tests for the Discourse forum activity collector."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from craft_dashboard.collectors.forum import (
    ForumCollector,
    _add_months,
    _flatten_categories,
    _is_retriable,
    _retry_after_seconds,
)
from craft_dashboard.config import ForumConfig
from craft_dashboard.models.forum import ForumBackfillState, ForumTopic
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"  # type: ignore[attr-defined]


def _make_topic(
    topic_id: int,
    *,
    created_at: str = "2024-03-05T10:00:00.000Z",
    last_posted_at: str | None = "2024-03-06T10:00:00.000Z",
    posts_count: int = 3,
) -> dict:
    return {
        "id": topic_id,
        "title": f"Topic {topic_id}",
        "posts_count": posts_count,
        "like_count": 1,
        "created_at": created_at,
        "last_posted_at": last_posted_at,
        "slug": f"topic-{topic_id}",
    }


def _category_page_response(
    base_url: str,
    slug: str,
    category_id: int,
    topics: list[dict],
    *,
    more: bool = False,
) -> httpx.Response:
    request = httpx.Request("GET", f"{base_url}/c/{slug}/{category_id}.json")
    more_url = f"/c/{slug}/{category_id}?order=created&page=1" if more else None
    return httpx.Response(
        200,
        json={"topic_list": {"topics": topics, "more_topics_url": more_url}},
        request=request,
    )


def _categories_response() -> httpx.Response:
    request = httpx.Request("GET", "https://forum.example.io/categories.json")
    return httpx.Response(
        200,
        json={
            "category_list": {
                "categories": [
                    {
                        "id": 7,
                        "slug": "bugs",
                        "subcategory_list": [{"id": 8, "slug": "bugs-sub"}],
                    },
                    {"id": 9, "slug": "general"},
                ]
            }
        },
        request=request,
    )


def _single_category_response(
    category_id: int = 7, slug: str = "bugs"
) -> httpx.Response:
    """A /categories.json response with exactly one (non-sub) category."""
    request = httpx.Request("GET", "https://forum.example.io/categories.json")
    return httpx.Response(
        200,
        json={"category_list": {"categories": [{"id": category_id, "slug": slug}]}},
        request=request,
    )


class TestDateHelpers:
    """Tests for the month-arithmetic helper."""

    def test_add_months_forward(self) -> None:
        assert _add_months(date(2024, 11, 15), 2) == date(2025, 1, 1)

    def test_add_months_backward(self) -> None:
        assert _add_months(date(2024, 1, 15), -1) == date(2023, 12, 1)


class TestFlattenCategories:
    """Tests for _flatten_categories."""

    def test_includes_subcategories(self) -> None:
        payload = {
            "category_list": {
                "categories": [
                    {
                        "id": 1,
                        "slug": "top",
                        "subcategory_list": [{"id": 2, "slug": "sub"}],
                    }
                ]
            }
        }
        result = _flatten_categories(payload)
        assert result == [
            {"id": 1, "slug": "top"},
            {"id": 2, "slug": "sub"},
        ]

    def test_missing_subcategory_list(self) -> None:
        payload = {"category_list": {"categories": [{"id": 1, "slug": "top"}]}}
        assert _flatten_categories(payload) == [{"id": 1, "slug": "top"}]

    def test_empty_payload(self) -> None:
        assert _flatten_categories({}) == []


class TestRetryPredicates:
    """Tests for _is_retriable / _retry_after_seconds."""

    def test_retries_on_429(self) -> None:
        request = httpx.Request("GET", "https://forum.example.io")
        exc = httpx.HTTPStatusError(
            "429", request=request, response=httpx.Response(429, request=request)
        )
        assert _is_retriable(exc) is True

    def test_does_not_retry_on_404(self) -> None:
        request = httpx.Request("GET", "https://forum.example.io")
        exc = httpx.HTTPStatusError(
            "404", request=request, response=httpx.Response(404, request=request)
        )
        assert _is_retriable(exc) is False

    def test_retries_on_transport_error(self) -> None:
        assert _is_retriable(httpx.ConnectTimeout("timeout")) is True

    def test_retry_after_seconds_parses_header(self) -> None:
        request = httpx.Request("GET", "https://forum.example.io")
        response = httpx.Response(429, request=request, headers={"Retry-After": "12"})
        exc = httpx.HTTPStatusError("429", request=request, response=response)
        assert _retry_after_seconds(exc) == 12.0

    def test_retry_after_seconds_missing_header(self) -> None:
        request = httpx.Request("GET", "https://forum.example.io")
        response = httpx.Response(429, request=request)
        exc = httpx.HTTPStatusError("429", request=request, response=response)
        assert _retry_after_seconds(exc) is None

    def test_retry_after_seconds_non_status_error(self) -> None:
        assert _retry_after_seconds(httpx.ConnectTimeout("timeout")) is None


@pytest.fixture
def forums() -> dict[str, ForumConfig]:
    return {
        "snapcraft": ForumConfig(
            base_url="https://forum.example.io", default_categories=["bugs"]
        )
    }


class TestHttpClient:
    """Tests for the lazily-created HTTP client."""

    def test_follows_redirects(self, forums: dict[str, ForumConfig]) -> None:
        """Discourse category slugs can be renamed, 301-redirecting the old
        slug's URL to the new one; the client must follow it rather than
        erroring, since the category id in the URL is still valid."""
        collector = ForumCollector(forums)
        assert collector.http.follow_redirects is True


@pytest.fixture
def collector(forums: dict[str, ForumConfig]) -> ForumCollector:
    return ForumCollector(forums, years_lookback=1)


class TestBackfillNextBatch:
    """Tests for ForumCollector.backfill_next_batch."""

    async def test_upserts_topics_from_single_category(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        responses = iter(
            [
                _single_category_response(),
                _category_page_response(
                    "https://forum.example.io",
                    "bugs",
                    7,
                    [_make_topic(1), _make_topic(2)],
                    more=False,
                ),
            ]
        )

        async def _fake_get(_self, url, params=None, **_kw):
            return next(responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            count = await collector.backfill_next_batch("snapcraft", test_db_session)

        assert count == 2
        rows = (
            (
                await test_db_session.execute(
                    select(ForumTopic).where(ForumTopic.forum == "snapcraft")
                )
            )
            .scalars()
            .all()
        )
        assert {r.external_id for r in rows} == {1, 2}
        assert rows[0].category == "bugs"

    async def test_marks_category_done_when_more_topics_url_is_null(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        responses = iter(
            [
                _single_category_response(),
                _category_page_response(
                    "https://forum.example.io", "bugs", 7, [_make_topic(1)], more=False
                ),
            ]
        )

        async def _fake_get(_self, url, params=None, **_kw):
            return next(responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            await collector.backfill_next_batch("snapcraft", test_db_session)

        state = await test_db_session.scalar(
            select(ForumBackfillState).where(ForumBackfillState.forum == "snapcraft")
        )
        assert state.category_progress["7"]["done"] is True

    async def test_resumes_from_persisted_page_cursor(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        """A second batch call fetches the *next* page, not page 0 again."""
        state = ForumBackfillState(
            forum="snapcraft",
            categories_cache=["bugs"],
            category_progress={"7": {"next_page": 3, "done": False}},
        )
        test_db_session.add(state)
        await test_db_session.commit()

        captured_params: list[dict] = []
        responses = iter(
            [
                _single_category_response(),
                _category_page_response(
                    "https://forum.example.io", "bugs", 7, [_make_topic(1)], more=False
                ),
            ]
        )

        async def _fake_get(_self, url, params=None, **_kw):
            if params is not None:
                captured_params.append(params)
            return next(responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            await collector.backfill_next_batch("snapcraft", test_db_session)

        page_params = [p for p in captured_params if "page" in p]
        assert page_params[0]["page"] == 3

    async def test_marks_category_done_when_cutoff_reached(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        """years_lookback=1: a topic older than that stops the category."""
        collector.years_lookback = 1
        old_topic = _make_topic(1, created_at="2000-01-01T00:00:00.000Z")
        responses = iter(
            [
                _single_category_response(),
                _category_page_response(
                    "https://forum.example.io", "bugs", 7, [old_topic], more=True
                ),
            ]
        )

        async def _fake_get(_self, url, params=None, **_kw):
            return next(responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            await collector.backfill_next_batch("snapcraft", test_db_session)

        state = await test_db_session.scalar(
            select(ForumBackfillState).where(ForumBackfillState.forum == "snapcraft")
        )
        assert state.category_progress["7"]["done"] is True

    async def test_fully_drains_a_single_large_category_within_budget(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        """A category spanning several pages is fully drained in one batch
        call rather than only advancing by a single page, so the request
        budget isn't wasted once only a few large categories remain."""
        collector.years_lookback = 50  # avoid the cutoff tripping on fixture dates
        responses = iter(
            [
                _single_category_response(),
                _category_page_response(
                    "https://forum.example.io", "bugs", 7, [_make_topic(1)], more=True
                ),
                _category_page_response(
                    "https://forum.example.io", "bugs", 7, [_make_topic(2)], more=True
                ),
                _category_page_response(
                    "https://forum.example.io", "bugs", 7, [_make_topic(3)], more=False
                ),
            ]
        )

        async def _fake_get(_self, url, params=None, **_kw):
            return next(responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            count = await collector.backfill_next_batch(
                "snapcraft", test_db_session, max_requests=10
            )

        assert count == 3
        state = await test_db_session.scalar(
            select(ForumBackfillState).where(ForumBackfillState.forum == "snapcraft")
        )
        assert state.category_progress["7"]["done"] is True

    async def test_respects_max_requests_across_categories(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        """max_requests=1 fetches only the first category's page this batch."""
        responses = iter(
            [
                _categories_response(),  # bugs(7), bugs-sub(8), general(9)
                _category_page_response(
                    "https://forum.example.io", "bugs", 7, [_make_topic(1)], more=False
                ),
            ]
        )

        async def _fake_get(_self, url, params=None, **_kw):
            return next(responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            count = await collector.backfill_next_batch(
                "snapcraft", test_db_session, max_requests=1
            )

        assert count == 1
        state = await test_db_session.scalar(
            select(ForumBackfillState).where(ForumBackfillState.forum == "snapcraft")
        )
        # Only category 7 was touched; 8 and 9 have no progress entry yet.
        assert set(state.category_progress) == {"7"}

    async def test_retries_on_429_then_succeeds(
        self, collector: ForumCollector, test_db_session: AsyncSession, monkeypatch
    ) -> None:
        """A 429 response triggers a retry (honoring Retry-After) rather than failing."""
        monkeypatch.setattr("asyncio.sleep", AsyncMock())
        request = httpx.Request("GET", "https://forum.example.io/c/bugs/7.json")
        responses = iter(
            [
                _single_category_response(),
                httpx.Response(429, request=request, headers={"Retry-After": "1"}),
                _category_page_response(
                    "https://forum.example.io", "bugs", 7, [_make_topic(1)], more=False
                ),
            ]
        )

        async def _fake_get(_self, url, params=None, **_kw):
            response = next(responses)
            response.request = request
            return response

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            count = await collector.backfill_next_batch("snapcraft", test_db_session)

        assert count == 1

    async def test_logs_batch_progress(
        self, collector: ForumCollector, test_db_session: AsyncSession, caplog
    ) -> None:
        caplog.set_level("INFO")
        responses = iter(
            [
                _single_category_response(),
                _category_page_response(
                    "https://forum.example.io", "bugs", 7, [_make_topic(1)], more=False
                ),
            ]
        )

        async def _fake_get(_self, url, params=None, **_kw):
            return next(responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            await collector.backfill_next_batch("snapcraft", test_db_session)

        assert "Forum backfill: snapcraft" in caplog.text
        assert "categories complete" in caplog.text

    async def test_already_done_categories_are_skipped(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        """A category marked done makes no further HTTP requests."""
        state = ForumBackfillState(
            forum="snapcraft",
            categories_cache=["bugs"],
            category_progress={"7": {"next_page": 5, "done": True}},
        )
        test_db_session.add(state)
        await test_db_session.commit()

        responses = iter([_single_category_response()])

        async def _fake_get(_self, url, params=None, **_kw):
            return next(responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            count = await collector.backfill_next_batch("snapcraft", test_db_session)

        assert count == 0


class TestRefreshCategories:
    """Tests for refresh_categories."""

    async def test_refresh_categories_caches_slugs(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        async def _fake_get(_self, url, params=None, **_kw):
            return _categories_response()

        with patch("httpx.AsyncClient.get", new=_fake_get):
            count = await collector.refresh_categories("snapcraft", test_db_session)

        assert count == 3
        state = await test_db_session.scalar(
            select(ForumBackfillState).where(ForumBackfillState.forum == "snapcraft")
        )
        assert state.categories_cache == ["bugs", "bugs-sub", "general"]
        assert state.categories_cached_at is not None

    async def test_refresh_categories_logs_progress(
        self, collector: ForumCollector, test_db_session: AsyncSession, caplog
    ) -> None:
        caplog.set_level("INFO")

        async def _fake_get(_self, url, params=None, **_kw):
            return _categories_response()

        with patch("httpx.AsyncClient.get", new=_fake_get):
            await collector.refresh_categories("snapcraft", test_db_session)

        assert "Forum categories cached: snapcraft" in caplog.text
        assert "3 categories" in caplog.text


class TestRefreshRecent:
    """Tests for the recent-topics refresh (current + previous month)."""

    async def test_refreshes_and_stamps_last_refresh(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        today = datetime.now(tz=UTC).date()
        recent_topic = _make_topic(1, created_at=f"{today.isoformat()}T00:00:00.000Z")
        responses = iter(
            [
                _single_category_response(),
                _category_page_response(
                    "https://forum.example.io",
                    "bugs",
                    7,
                    [recent_topic],
                    more=False,
                ),
            ]
        )

        async def _fake_get(_self, url, params=None, **_kw):
            return next(responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            count = await collector.refresh_recent("snapcraft", test_db_session)

        assert count == 1
        state = await test_db_session.scalar(
            select(ForumBackfillState).where(ForumBackfillState.forum == "snapcraft")
        )
        assert state.last_incremental_refresh_at is not None

    async def test_stops_once_older_than_previous_month(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        """Scanning stops once a topic older than the previous month appears,
        without fetching further pages."""
        old_topic = _make_topic(1, created_at="2000-01-01T00:00:00.000Z")
        responses = iter(
            [
                _single_category_response(),
                _category_page_response(
                    "https://forum.example.io", "bugs", 7, [old_topic], more=True
                ),
            ]
        )

        async def _fake_get(_self, url, params=None, **_kw):
            return next(responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            count = await collector.refresh_recent("snapcraft", test_db_session)

        # Only one page was consumed from the iterator (StopIteration would
        # be raised on a second httpx.AsyncClient.get call if pagination
        # continued past the old topic).
        assert count == 1

    async def test_logs_progress_with_topics_updated(
        self, collector: ForumCollector, test_db_session: AsyncSession, caplog
    ) -> None:
        caplog.set_level("INFO")

        async def _fake_get(_self, url, params=None, **_kw):
            if "categories.json" in str(url):
                return _single_category_response()
            return _category_page_response(
                "https://forum.example.io", "bugs", 7, [_make_topic(1)], more=False
            )

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            await collector.refresh_recent("snapcraft", test_db_session)

        assert "Forum refresh: snapcraft" in caplog.text
        assert "topics updated" in caplog.text
