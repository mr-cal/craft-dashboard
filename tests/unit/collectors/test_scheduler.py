"""Tests for the refresh scheduler."""

from datetime import UTC, datetime, timedelta

from craft_dashboard.collectors.scheduler import (
    distribute_refresh_dates,
    is_due_for_refresh,
)


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
