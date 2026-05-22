"""Tests for the refresh scheduler."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from craft_dashboard.collectors.scheduler import (
    distribute_refresh_dates,
    is_due_for_refresh,
    record_refresh_error,
)
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


class TestDistributeRefreshDates:
    """Tests for distribute_refresh_dates."""

    def test_distributes_evenly(self) -> None:
        """Projects are spread across the interval."""
        project_ids = [1, 2, 3]
        interval_days = 7

        result = distribute_refresh_dates(project_ids, interval_days)

        assert len(result) == 3
        # All dates should be in the future
        now = datetime.now(tz=UTC)
        for pid, dt in result:
            assert dt > now
            assert pid in project_ids

    def test_empty_projects(self) -> None:
        """Empty project list returns empty result."""
        result = distribute_refresh_dates([], 7)

        assert result == []

    def test_single_project(self) -> None:
        """Single project gets first slot."""
        result = distribute_refresh_dates([42], 7)

        assert len(result) == 1
        assert result[0][0] == 42

    def test_two_projects_spacing(self) -> None:
        """Two projects should be approximately half the interval apart."""
        result = distribute_refresh_dates([1, 2], interval_days=2)
        assert len(result) == 2
        gap = (result[1][1] - result[0][1]).total_seconds()
        assert 86300 < gap < 86500

    def test_many_projects(self) -> None:
        """Many projects all get unique times."""
        ids = list(range(1, 21))
        result = distribute_refresh_dates(ids, interval_days=7)
        assert len(result) == 20
        times = [dt for _, dt in result]
        assert len(set(times)) == 20


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
