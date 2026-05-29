"""Tests for issue age computation."""

from datetime import UTC, datetime, timedelta

from craft_dashboard.routes.issues import _compute_age_days


class TestComputeAgeDays:
    def test_none_created_at_returns_none(self):
        """Issues with no creation date should return None, not 0."""
        result = _compute_age_days(None)
        assert result is None

    def test_valid_created_at_returns_int(self):
        """Issues with a creation date should return an integer."""
        created = datetime.now(tz=UTC) - timedelta(days=10)
        result = _compute_age_days(created)
        assert result == 10
