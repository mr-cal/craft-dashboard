"""Unit tests for the Engagement forum activity routes."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.config import DashboardConfig, ForumConfig
from craft_dashboard.dependencies import get_config, get_db_session
from craft_dashboard.models.forum import ForumBackfillState, ForumTopic
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"  # type: ignore[attr-defined]


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Skip real startup for tests."""
    yield


def _config() -> DashboardConfig:
    return DashboardConfig(
        craft_applications=["snapcraft"],
        maintainers=["alice"],
        forums={
            "snapcraft": ForumConfig(
                base_url="https://forum.snapcraft.io",
                default_categories=["general"],
            ),
            "charmcraft": ForumConfig(
                base_url="https://discourse.charmhub.io",
                display_name="charmhub forums",
            ),
        },
    )


@pytest.fixture
def test_client(test_db_session: AsyncSession) -> AsyncGenerator[TestClient, None]:
    """Test client wired to the in-memory test session and a 2-forum config."""
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.state.config = _config()

    async def _override_config() -> DashboardConfig:
        return app.state.config

    async def _override_session() -> AsyncGenerator[AsyncSession, None]:
        yield test_db_session

    app.dependency_overrides[get_db_session] = _override_session
    app.dependency_overrides[get_config] = _override_config

    with TestClient(app) as client:
        yield client


def _topic(
    forum: str,
    external_id: int,
    *,
    created_at: datetime,
    posts_count: int = 5,
    category: str = "general",
) -> ForumTopic:
    return ForumTopic(
        forum=forum,
        category=category,
        external_id=external_id,
        title=f"Topic {external_id}",
        posts_count=posts_count,
        like_count=0,
        created_at=created_at,
        last_fetched_at=datetime.now(tz=UTC),
    )


class TestForumsPage:
    """Tests for GET /engagement/forums."""

    def test_renders_one_section_per_configured_forum(
        self, test_client: TestClient
    ) -> None:
        response = test_client.get("/engagement/forums")

        assert response.status_code == 200
        assert 'data-forum-section="snapcraft"' in response.text
        assert 'data-forum-section="charmcraft"' in response.text

    def test_uses_configured_display_name_with_fallback(
        self, test_client: TestClient
    ) -> None:
        """snapcraft has no display_name configured in _config(), so falls
        back to "{name} forum"; charmcraft's configured display_name is used
        verbatim."""
        response = test_client.get("/engagement/forums")

        assert response.status_code == 200
        assert "snapcraft forum<" in response.text
        assert "charmhub forums<" in response.text

    def test_nav_shows_engagement_link(self, test_client: TestClient) -> None:
        response = test_client.get("/engagement/forums")

        assert response.status_code == 200
        assert 'href="/engagement/forums"' in response.text

    def test_embeds_default_categories_for_checkbox_prechecking(
        self, test_client: TestClient
    ) -> None:
        response = test_client.get("/engagement/forums")

        assert response.status_code == 200
        assert '"default_categories": ["general"]' in response.text

    async def test_includes_cached_categories_from_backfill_state(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        test_db_session.add(
            ForumBackfillState(
                forum="snapcraft",
                categories_cache=["general", "questions"],
            )
        )
        await test_db_session.commit()

        response = test_client.get("/engagement/forums")

        assert response.status_code == 200
        assert '"questions"' in response.text


class TestForumsData:
    """Tests for GET /engagement/forums/data."""

    def test_unknown_forum_returns_404(self, test_client: TestClient) -> None:
        response = test_client.get("/engagement/forums/data?forum=doesnotexist")

        assert response.status_code == 404

    async def test_buckets_topics_per_day(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        test_db_session.add_all(
            [
                _topic(
                    "snapcraft",
                    1,
                    created_at=datetime(2024, 3, 5, tzinfo=UTC),
                    posts_count=4,
                    category="bugs",
                ),
                _topic(
                    "snapcraft",
                    2,
                    created_at=datetime(2024, 3, 5, tzinfo=UTC),
                    posts_count=6,
                    category="questions",
                ),
                _topic(
                    "snapcraft",
                    3,
                    created_at=datetime(2024, 4, 1, tzinfo=UTC),
                    posts_count=2,
                    category="bugs",
                ),
            ]
        )
        await test_db_session.commit()

        response = test_client.get("/engagement/forums/data?forum=snapcraft")

        assert response.status_code == 200
        data = response.json()
        assert data["days"] == ["2024-03-05", "2024-04-01"]
        assert data["all"] == [2, 1]
        assert data["categories"]["bugs"] == [1, 1]
        assert data["categories"]["questions"] == [1, 0]

    async def test_forum_known_via_backfill_state_but_no_topics_returns_empty_series(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        """A forum known via ForumBackfillState (categories refreshed) but
        with no topics backfilled yet should return empty series, not 404."""
        test_db_session.add(
            ForumBackfillState(forum="charmcraft", categories_cache=["general"])
        )
        await test_db_session.commit()

        response = test_client.get("/engagement/forums/data?forum=charmcraft")

        assert response.status_code == 200
        data = response.json()
        assert data["days"] == []
        assert data["all"] == []
