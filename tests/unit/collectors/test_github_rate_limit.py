"""Tests for GitHub collector rate limit handling."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from craft_dashboard.collectors import RateLimitError
from craft_dashboard.collectors.github import GitHubCollector

_TOKEN = "placeholder-token"


class FrozenDateTime(datetime):
    """Frozen datetime for deterministic rate limit waits."""

    frozen_now = datetime(2025, 1, 10, 12, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.frozen_now.replace(tzinfo=None)
        return cls.frozen_now.astimezone(tz)


class TestGitHubRateLimit:
    def test_check_rate_limit_returns_core_quota_details(self) -> None:
        collector = GitHubCollector(token=_TOKEN, org="canonical")
        collector.gh = MagicMock()
        collector.gh.get_rate_limit.return_value = SimpleNamespace(
            rate=SimpleNamespace(
                remaining=123,
                limit=5000,
                reset=datetime(2025, 1, 10, 12, 30, tzinfo=UTC),
            )
        )

        result = collector.check_rate_limit()

        assert result == {
            "remaining": 123,
            "limit": 5000,
            "reset_at": datetime(2025, 1, 10, 12, 30, tzinfo=UTC),
        }

    def test_wait_for_rate_limit_sleeps_until_reset_when_quota_is_low(self) -> None:
        collector = GitHubCollector(token=_TOKEN, org="canonical")
        collector.check_rate_limit = MagicMock(
            return_value={
                "remaining": 25,
                "limit": 5000,
                "reset_at": datetime(2025, 1, 10, 12, 2, 30, tzinfo=UTC),
            }
        )

        with (
            patch("craft_dashboard.collectors.github.datetime", FrozenDateTime),
            patch("craft_dashboard.collectors.github.time.sleep") as sleep,
        ):
            collector.wait_for_rate_limit(min_remaining=100)

        sleep.assert_called_once_with(150)

    def test_wait_for_rate_limit_raises_when_reset_time_is_missing(self) -> None:
        collector = GitHubCollector(token=_TOKEN, org="canonical")
        collector.check_rate_limit = MagicMock(
            return_value={
                "remaining": 0,
                "limit": 5000,
                "reset_at": None,
            }
        )

        with pytest.raises(RateLimitError, match="reset time"):
            collector.wait_for_rate_limit(min_remaining=100)
