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
            resources=SimpleNamespace(
                core=SimpleNamespace(
                    remaining=123,
                    limit=5000,
                    reset=datetime(2025, 1, 10, 12, 30, tzinfo=UTC),
                ),
                graphql=SimpleNamespace(
                    remaining=456,
                    limit=5000,
                    reset=datetime(2025, 1, 10, 12, 45, tzinfo=UTC),
                ),
            )
        )

        result = collector.check_rate_limit()

        assert result == {
            "core_remaining": 123,
            "core_limit": 5000,
            "core_reset": datetime(2025, 1, 10, 12, 30, tzinfo=UTC),
            "graphql_remaining": 456,
            "graphql_limit": 5000,
            "graphql_reset": datetime(2025, 1, 10, 12, 45, tzinfo=UTC),
        }

    def test_check_rate_limit_reads_graphql_from_resources(self) -> None:
        collector = GitHubCollector(token=_TOKEN, org="canonical")
        collector.gh = MagicMock()
        collector.gh.get_rate_limit.return_value = SimpleNamespace(
            resources=SimpleNamespace(
                core=SimpleNamespace(
                    remaining=123,
                    limit=5000,
                    reset=datetime(2025, 1, 10, 12, 30, tzinfo=UTC),
                ),
                graphql=SimpleNamespace(
                    remaining=789,
                    limit=5000,
                    reset=datetime(2025, 1, 10, 12, 50, tzinfo=UTC),
                ),
            ),
            graphql=SimpleNamespace(
                remaining=1,
                limit=1,
                reset=datetime(2025, 1, 10, 12, 1, tzinfo=UTC),
            ),
        )

        result = collector.check_rate_limit()

        assert result["graphql_remaining"] == 789
        assert result["graphql_limit"] == 5000
        assert result["graphql_reset"] == datetime(2025, 1, 10, 12, 50, tzinfo=UTC)

    def test_wait_for_rate_limit_sleeps_until_reset_when_quota_is_low(self) -> None:
        collector = GitHubCollector(token=_TOKEN, org="canonical")
        collector.check_rate_limit = MagicMock(
            return_value={
                "core_remaining": 25,
                "core_limit": 5000,
                "core_reset": datetime(2025, 1, 10, 12, 2, 30, tzinfo=UTC),
                "graphql_remaining": 250,
                "graphql_limit": 5000,
                "graphql_reset": datetime(2025, 1, 10, 13, 0, tzinfo=UTC),
            }
        )

        with (
            patch("craft_dashboard.collectors.github.datetime", FrozenDateTime),
            patch("craft_dashboard.collectors.github.time.sleep") as sleep,
        ):
            collector.wait_for_rate_limit(resource="core", threshold=100)

        sleep.assert_called_once_with(150)

    def test_wait_for_rate_limit_sleeps_until_graphql_reset_when_quota_is_low(
        self,
    ) -> None:
        collector = GitHubCollector(token=_TOKEN, org="canonical")
        collector.check_rate_limit = MagicMock(
            return_value={
                "core_remaining": 250,
                "core_limit": 5000,
                "core_reset": datetime(2025, 1, 10, 13, 0, tzinfo=UTC),
                "graphql_remaining": 25,
                "graphql_limit": 5000,
                "graphql_reset": datetime(2025, 1, 10, 12, 2, 30, tzinfo=UTC),
            }
        )

        with (
            patch("craft_dashboard.collectors.github.datetime", FrozenDateTime),
            patch("craft_dashboard.collectors.github.time.sleep") as sleep,
        ):
            collector.wait_for_rate_limit(resource="graphql", threshold=100)

        sleep.assert_called_once_with(150)

    def test_wait_for_rate_limit_defaults_to_core_resource(self) -> None:
        collector = GitHubCollector(token=_TOKEN, org="canonical")
        collector.check_rate_limit = MagicMock(
            return_value={
                "core_remaining": 25,
                "core_limit": 5000,
                "core_reset": datetime(2025, 1, 10, 12, 2, 30, tzinfo=UTC),
                "graphql_remaining": 250,
                "graphql_limit": 5000,
                "graphql_reset": datetime(2025, 1, 10, 13, 0, tzinfo=UTC),
            }
        )

        with (
            patch("craft_dashboard.collectors.github.datetime", FrozenDateTime),
            patch("craft_dashboard.collectors.github.time.sleep") as sleep,
        ):
            collector.wait_for_rate_limit()

        sleep.assert_called_once_with(150)

    def test_wait_for_rate_limit_raises_when_reset_time_is_missing(self) -> None:
        collector = GitHubCollector(token=_TOKEN, org="canonical")
        collector.check_rate_limit = MagicMock(
            return_value={
                "core_remaining": 0,
                "core_limit": 5000,
                "core_reset": None,
                "graphql_remaining": 250,
                "graphql_limit": 5000,
                "graphql_reset": datetime(2025, 1, 10, 13, 0, tzinfo=UTC),
            }
        )

        with pytest.raises(RateLimitError, match="rate limit exhausted") as exc_info:
            collector.wait_for_rate_limit(resource="core", threshold=100)

        assert exc_info.value.resource == "core"
        assert exc_info.value.remaining == 0
        assert exc_info.value.limit == 5000
