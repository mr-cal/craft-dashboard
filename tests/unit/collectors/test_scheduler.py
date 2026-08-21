"""Tests for the refresh scheduler."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from craft_dashboard.collectors.scheduler import (
    get_least_recently_refreshed,
    is_due_for_refresh,
    record_refresh_error,
)
from craft_dashboard.models.project import Project
from craft_dashboard.models.refresh_schedule import RefreshSchedule
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


class TestIsDueForRefresh:
    """Tests for is_due_for_refresh."""

    def test_none_next_refresh_is_due(self) -> None:
        """If next_refresh_at is None, it's due."""
        assert is_due_for_refresh(next_refresh_at=None) is True

    def test_past_date_is_due(self) -> None:
        """If next_refresh_at is in the past, it's due."""
        past = datetime.now(tz=UTC) - timedelta(hours=1)
        assert is_due_for_refresh(next_refresh_at=past) is True

    def test_future_date_not_due(self) -> None:
        """If next_refresh_at is in the future, it's not due."""
        future = datetime.now(tz=UTC) + timedelta(hours=1)
        assert is_due_for_refresh(next_refresh_at=future) is False

    def test_exactly_now_is_due(self) -> None:
        """A refresh time of exactly now is due."""
        now = datetime.now(tz=UTC)
        assert is_due_for_refresh(next_refresh_at=now) is True

    def test_one_second_future_not_due(self) -> None:
        """One second in the future is not due."""
        future = datetime.now(tz=UTC) + timedelta(seconds=1)
        assert is_due_for_refresh(next_refresh_at=future) is False


class TestGetLeastRecentlyRefreshed:
    """Tests for get_least_recently_refreshed (the hourly rotation selector)."""

    async def test_no_projects_returns_none(self, test_db_session) -> None:
        """With no projects at all, there's nothing to rotate to."""
        assert await get_least_recently_refreshed(test_db_session) is None

    async def test_never_refreshed_project_wins(self, test_db_session) -> None:
        """A project with no RefreshSchedule row at all is most overdue."""
        test_db_session.add_all(
            [
                Project(id=1, name="charmcraft", category="application"),
                Project(id=2, name="rockcraft", category="application"),
            ]
        )
        await test_db_session.flush()
        test_db_session.add(
            RefreshSchedule(
                project_id=2,
                source="github",
                last_refreshed_at=datetime.now(tz=UTC) - timedelta(hours=1),
            )
        )
        await test_db_session.commit()

        result = await get_least_recently_refreshed(test_db_session)

        assert result == (1, "charmcraft", "github")

    async def test_oldest_last_refreshed_wins(self, test_db_session) -> None:
        """The project refreshed longest ago is picked over a recently-refreshed one."""
        now = datetime.now(tz=UTC)
        test_db_session.add_all(
            [
                Project(id=1, name="charmcraft", category="application"),
                Project(id=2, name="rockcraft", category="application"),
            ]
        )
        await test_db_session.flush()
        test_db_session.add_all(
            [
                RefreshSchedule(
                    project_id=1,
                    source="github",
                    last_refreshed_at=now - timedelta(hours=1),
                ),
                RefreshSchedule(
                    project_id=2,
                    source="github",
                    last_refreshed_at=now - timedelta(hours=5),
                ),
            ]
        )
        await test_db_session.commit()

        result = await get_least_recently_refreshed(test_db_session)

        assert result == (2, "rockcraft", "github")

    async def test_launchpad_project_included(self, test_db_session) -> None:
        """Launchpad projects (category='launchpad') are eligible and labeled correctly."""
        now = datetime.now(tz=UTC)
        test_db_session.add_all(
            [
                Project(id=1, name="charmcraft", category="application"),
                Project(id=2, name="snapcraft (launchpad)", category="launchpad"),
            ]
        )
        await test_db_session.flush()
        test_db_session.add_all(
            [
                RefreshSchedule(
                    project_id=1,
                    source="github",
                    last_refreshed_at=now - timedelta(hours=1),
                ),
                RefreshSchedule(
                    project_id=2,
                    source="launchpad",
                    last_refreshed_at=now - timedelta(days=1),
                ),
            ]
        )
        await test_db_session.commit()

        result = await get_least_recently_refreshed(test_db_session)

        assert result == (2, "snapcraft (launchpad)", "launchpad")

    async def test_aggregate_project_excluded(self, test_db_session) -> None:
        """The synthetic 'all-projects' aggregate row is never selected."""
        test_db_session.add_all(
            [
                Project(id=1, name="all-projects", category="aggregate"),
                Project(id=2, name="charmcraft", category="application"),
            ]
        )
        await test_db_session.flush()
        test_db_session.add(
            RefreshSchedule(
                project_id=2,
                source="github",
                last_refreshed_at=datetime.now(tz=UTC),
            )
        )
        await test_db_session.commit()

        result = await get_least_recently_refreshed(test_db_session)

        assert result == (2, "charmcraft", "github")


class TestRecordRefreshError:
    async def test_record_error_creates_new_schedule(self, test_db_session) -> None:
        """When no schedule exists, record_refresh_error creates one."""
        with patch("sqlalchemy.dialects.postgresql.insert", side_effect=sqlite_insert):
            await record_refresh_error(999, "github", "test error", test_db_session)

        result = await test_db_session.execute(
            select(RefreshSchedule).where(
                RefreshSchedule.project_id == 999,
                RefreshSchedule.source == "github",
            )
        )
        schedule = result.scalar_one()
        assert schedule.last_error == "test error"
        assert schedule.consecutive_failures == 1
