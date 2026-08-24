"""Tests for the forum activity scheduling logic in scripts/collect_forum_data.py.

Covers backfill/refresh resumability per the acceptance criteria: a missed
5-day refresh window self-corrects, and a partial historical backfill
resumes rather than restarting.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from craft_dashboard.models.forum import ForumBackfillState
from scripts import collect_forum_data
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"  # type: ignore[attr-defined]


class _FakeCollector:
    """Records which forums backfill_next_batch/refresh_recent were called for."""

    def __init__(self) -> None:
        self.backfill_calls: list[str] = []
        self.refresh_calls: list[str] = []

    async def backfill_next_batch(
        self, forum: str, _session: AsyncSession, *, max_requests: int = 300
    ) -> int:
        self.backfill_calls.append(forum)
        return 1

    async def refresh_recent(self, forum: str, _session: AsyncSession) -> int:
        self.refresh_calls.append(forum)
        return 1


@pytest.fixture
def session_factory(
    test_db_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        test_db_engine, class_=AsyncSession, expire_on_commit=False
    )


class TestRunBackfill:
    """Tests for _run_backfill: one batch per forum, per call."""

    async def test_calls_backfill_next_batch_for_every_forum(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        collector = _FakeCollector()

        total = await collect_forum_data._run_backfill(
            collector, session_factory, ["snapcraft", "charmcraft"], 300
        )

        assert total == 2
        assert collector.backfill_calls == ["snapcraft", "charmcraft"]

    async def test_continues_past_a_failing_forum(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """One forum's backfill raising shouldn't stop the others."""
        collector = _FakeCollector()
        collector.backfill_next_batch = AsyncMock(side_effect=[RuntimeError("boom"), 1])

        total = await collect_forum_data._run_backfill(
            collector, session_factory, ["snapcraft", "charmcraft"], 300
        )

        assert total == 1
        assert collector.backfill_next_batch.await_count == 2


class TestRunRefresh:
    """Tests for _run_refresh: skip-if-recent, self-healing on missed cycles."""

    async def test_refreshes_forum_never_refreshed_before(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        collector = _FakeCollector()

        await collect_forum_data._run_refresh(
            collector, session_factory, ["snapcraft"], refresh_interval_days=5
        )

        assert collector.refresh_calls == ["snapcraft"]

    async def test_skips_forum_refreshed_recently(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        async with session_factory() as session:
            session.add(
                ForumBackfillState(
                    forum="snapcraft",
                    last_incremental_refresh_at=datetime.now(tz=UTC)
                    - timedelta(days=1),
                    categories_cache=[],
                )
            )
            await session.commit()

        collector = _FakeCollector()
        await collect_forum_data._run_refresh(
            collector, session_factory, ["snapcraft"], refresh_interval_days=5
        )

        assert collector.refresh_calls == []

    async def test_self_heals_after_a_missed_refresh_cycle(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """A refresh that's 6 days overdue (missed the 5-day window) still
        gets refreshed on the next run — no separate catch-up logic needed."""
        async with session_factory() as session:
            session.add(
                ForumBackfillState(
                    forum="snapcraft",
                    last_incremental_refresh_at=datetime.now(tz=UTC)
                    - timedelta(days=6),
                    categories_cache=[],
                )
            )
            await session.commit()

        collector = _FakeCollector()
        await collect_forum_data._run_refresh(
            collector, session_factory, ["snapcraft"], refresh_interval_days=5
        )

        assert collector.refresh_calls == ["snapcraft"]

    async def test_only_overdue_forums_are_refreshed(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Independent per-forum scheduling: one recent, one overdue."""
        async with session_factory() as session:
            session.add_all(
                [
                    ForumBackfillState(
                        forum="snapcraft",
                        last_incremental_refresh_at=datetime.now(tz=UTC)
                        - timedelta(days=1),
                        categories_cache=[],
                    ),
                    ForumBackfillState(
                        forum="charmcraft",
                        last_incremental_refresh_at=datetime.now(tz=UTC)
                        - timedelta(days=10),
                        categories_cache=[],
                    ),
                ]
            )
            await session.commit()

        collector = _FakeCollector()
        await collect_forum_data._run_refresh(
            collector,
            session_factory,
            ["snapcraft", "charmcraft"],
            refresh_interval_days=5,
        )

        assert collector.refresh_calls == ["charmcraft"]
