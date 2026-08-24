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
    _month_bounds,
    _months_between,
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
    category_id: int = 7,
    posts_count: int = 3,
) -> dict:
    return {
        "id": topic_id,
        "title": f"Topic {topic_id}",
        "posts_count": posts_count,
        "like_count": 1,
        "created_at": created_at,
        "last_posted_at": last_posted_at,
        "category_id": category_id,
        "slug": f"topic-{topic_id}",
    }


def _search_response(topics: list[dict]) -> httpx.Response:
    request = httpx.Request("GET", "https://forum.example.io/search.json")
    return httpx.Response(200, json={"topics": topics}, request=request)


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


class TestDateHelpers:
    """Tests for the month/date bucketing helpers."""

    def test_month_bounds_returns_inclusive_start_exclusive_end(self) -> None:
        start, end = _month_bounds(2024, 2)
        assert start == date(2024, 2, 1)
        assert end == date(2024, 3, 1)

    def test_month_bounds_handles_december(self) -> None:
        start, end = _month_bounds(2024, 12)
        assert start == date(2024, 12, 1)
        assert end == date(2025, 1, 1)

    def test_add_months_forward(self) -> None:
        assert _add_months(date(2024, 11, 15), 2) == date(2025, 1, 1)

    def test_add_months_backward(self) -> None:
        assert _add_months(date(2024, 1, 15), -1) == date(2023, 12, 1)

    def test_months_between(self) -> None:
        assert _months_between(date(2023, 1, 1), date(2024, 3, 1)) == 14


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


@pytest.fixture
def collector(forums: dict[str, ForumConfig]) -> ForumCollector:
    return ForumCollector(forums, years_lookback=1)


class TestBackfillMonth:
    """Tests for ForumCollector.backfill_month."""

    async def test_upserts_topics_and_dedupes_by_id(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        """Two pages of results, second empty, stop after first non-empty page < page size."""
        responses = iter(
            [
                _categories_response(),
                _search_response([_make_topic(1), _make_topic(2)]),
            ]
        )

        async def _fake_get(_self, url, params=None, **_kw):
            return next(responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            count = await collector.backfill_month(
                "snapcraft", 2024, 3, test_db_session
            )

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

    async def test_stops_when_page_returns_no_new_topics(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        """A repeated/duplicate page (same topic ids) halts pagination."""
        responses = iter(
            [
                _categories_response(),
                _search_response([_make_topic(1)] * 50),  # full page, same id repeated
                _search_response([_make_topic(1)]),  # page 2: no new topics
            ]
        )

        async def _fake_get(_self, url, params=None, **_kw):
            return next(responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            count = await collector.backfill_month(
                "snapcraft", 2024, 3, test_db_session
            )

        assert count == 1

    async def test_upsert_refreshes_existing_topic(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        """Re-running backfill_month for the same topic updates posts_count."""
        first_responses = iter(
            [_categories_response(), _search_response([_make_topic(1, posts_count=3)])]
        )
        second_responses = iter(
            [_categories_response(), _search_response([_make_topic(1, posts_count=9)])]
        )

        async def _fake_get_1(_self, url, params=None, **_kw):
            return next(first_responses)

        async def _fake_get_2(_self, url, params=None, **_kw):
            return next(second_responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get_1),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            await collector.backfill_month("snapcraft", 2024, 3, test_db_session)

        collector._category_map_cache.clear()
        with (
            patch("httpx.AsyncClient.get", new=_fake_get_2),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            await collector.backfill_month("snapcraft", 2024, 3, test_db_session)

        rows = (
            (
                await test_db_session.execute(
                    select(ForumTopic).where(ForumTopic.external_id == 1)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].posts_count == 9

    async def test_retries_on_429_then_succeeds(
        self, collector: ForumCollector, test_db_session: AsyncSession, monkeypatch
    ) -> None:
        """A 429 response triggers a retry (honoring Retry-After) rather than failing."""
        monkeypatch.setattr("asyncio.sleep", AsyncMock())
        request = httpx.Request("GET", "https://forum.example.io/search.json")
        responses = iter(
            [
                _categories_response(),
                httpx.Response(429, request=request, headers={"Retry-After": "1"}),
                _search_response([_make_topic(1)]),
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
            count = await collector.backfill_month(
                "snapcraft", 2024, 3, test_db_session
            )

        assert count == 1

    async def test_query_uses_month_boundary_date_filter(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        """The search query's after:/before: params match the requested month."""
        captured_params: list[dict] = []
        responses = iter([_categories_response(), _search_response([])])

        async def _fake_get(_self, url, params=None, **_kw):
            if params is not None:
                captured_params.append(params)
            return next(responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            await collector.backfill_month("snapcraft", 2024, 2, test_db_session)

        search_params = [p for p in captured_params if "q" in p]
        assert search_params
        assert "after:2024-02-01" in search_params[0]["q"]
        assert "before:2024-03-01" in search_params[0]["q"]


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


class TestBackfillNextMonth:
    """Tests for resumable historical backfill."""

    async def test_first_run_starts_at_current_month(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        responses = iter([_categories_response(), _search_response([_make_topic(1)])])

        async def _fake_get(_self, url, params=None, **_kw):
            return next(responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            await collector.backfill_next_month("snapcraft", test_db_session)

        today = datetime.now(tz=UTC).date()
        state = await test_db_session.scalar(
            select(ForumBackfillState).where(ForumBackfillState.forum == "snapcraft")
        )
        assert state.earliest_month_backfilled.year == today.year
        assert state.earliest_month_backfilled.month == today.month

    async def test_resumes_from_earliest_covered_month(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        """A partial backfill (earliest_month_backfilled already set) resumes
        from the month *before* it, not from the current month again."""
        collector.years_lookback = 10
        state = ForumBackfillState(
            forum="snapcraft",
            earliest_month_backfilled=datetime(2024, 3, 1, tzinfo=UTC),
            categories_cache=[],
        )
        test_db_session.add(state)
        await test_db_session.commit()

        responses = iter([_categories_response(), _search_response([_make_topic(1)])])
        captured_params: list[dict] = []

        async def _fake_get(_self, url, params=None, **_kw):
            if params is not None:
                captured_params.append(params)
            return next(responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            await collector.backfill_next_month("snapcraft", test_db_session)

        search_params = [p for p in captured_params if "q" in p]
        assert "after:2024-02-01" in search_params[0]["q"]
        assert "before:2024-03-01" in search_params[0]["q"]

    async def test_stops_once_lookback_window_covered(
        self, collector: ForumCollector, test_db_session: AsyncSession, caplog
    ) -> None:
        """years_lookback=1: once earliest covers 1 year back, backfill is a no-op."""
        caplog.set_level("INFO")
        today = datetime.now(tz=UTC).date()
        oldest = _add_months(date(today.year, today.month, 1), -12)
        state = ForumBackfillState(
            forum="snapcraft",
            earliest_month_backfilled=datetime(
                oldest.year, oldest.month, 1, tzinfo=UTC
            ),
            categories_cache=[],
        )
        test_db_session.add(state)
        await test_db_session.commit()

        count = await collector.backfill_next_month("snapcraft", test_db_session)

        assert count == 0
        assert "fully backfilled" in caplog.text

    async def test_logs_running_total_and_months_remaining(
        self, collector: ForumCollector, test_db_session: AsyncSession, caplog
    ) -> None:
        caplog.set_level("INFO")
        responses = iter([_categories_response(), _search_response([_make_topic(1)])])

        async def _fake_get(_self, url, params=None, **_kw):
            return next(responses)

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            await collector.backfill_next_month("snapcraft", test_db_session)

        assert "months remaining" in caplog.text
        assert "running total covers back to" in caplog.text


class TestRefreshRecent:
    """Tests for the recent-month refresh (current + conditional previous)."""

    async def test_refreshes_only_current_month_when_never_refreshed(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        """last_incremental_refresh_at is None: previous month is always
        eligible too (never refreshed before), matching backfill_month calls."""
        call_count = 0

        async def _fake_get(_self, url, params=None, **_kw):
            nonlocal call_count
            call_count += 1
            if "categories.json" in str(url):
                return _categories_response()
            return _search_response([])

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            await collector.refresh_recent("snapcraft", test_db_session)

        state = await test_db_session.scalar(
            select(ForumBackfillState).where(ForumBackfillState.forum == "snapcraft")
        )
        assert state.last_incremental_refresh_at is not None

    async def test_does_not_refresh_previous_month_if_already_refreshed(
        self, collector: ForumCollector, test_db_session: AsyncSession
    ) -> None:
        """If last refresh happened after the previous month ended, only the
        current month is re-fetched (self-healing but not wasteful)."""
        today = datetime.now(tz=UTC).date()
        state = ForumBackfillState(
            forum="snapcraft",
            # Refreshed "just now" — after the previous month certainly ended.
            last_incremental_refresh_at=datetime.now(tz=UTC),
            categories_cache=[],
        )
        test_db_session.add(state)
        await test_db_session.commit()

        search_calls: list[str] = []

        async def _fake_get(_self, url, params=None, **_kw):
            if "categories.json" in str(url):
                return _categories_response()
            search_calls.append(params["q"])
            return _search_response([])

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            await collector.refresh_recent("snapcraft", test_db_session)

        # Only the current month should have been queried.
        current_month_marker = f"after:{today.year:04d}-{today.month:02d}"
        assert len(search_calls) == 1
        assert current_month_marker in search_calls[0]

    async def test_logs_progress_with_topics_updated(
        self, collector: ForumCollector, test_db_session: AsyncSession, caplog
    ) -> None:
        caplog.set_level("INFO")

        async def _fake_get(_self, url, params=None, **_kw):
            if "categories.json" in str(url):
                return _categories_response()
            return _search_response([_make_topic(1)])

        with (
            patch("httpx.AsyncClient.get", new=_fake_get),
            patch("craft_dashboard.collectors.forum.insert", new=sqlite_insert),
        ):
            await collector.refresh_recent("snapcraft", test_db_session)

        assert "Forum refresh: snapcraft" in caplog.text
        assert "topics updated" in caplog.text
