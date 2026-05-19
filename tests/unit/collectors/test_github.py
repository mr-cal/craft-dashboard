"""Tests for the GitHub data collector."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from craft_dashboard.collectors.github import (
    GitHubCollector,
    _classify_issue,
    _compute_issue_hash,
)


class TestClassifyIssue:
    """Tests for _classify_issue helper."""

    def test_classify_open_issue(self) -> None:
        """Open issue with no pull_request attribute is classified as 'issue'."""
        gh_issue = MagicMock()
        gh_issue.pull_request = None
        gh_issue.state = "open"

        result = _classify_issue(gh_issue)

        assert result == ("issue", "open")

    def test_classify_open_pr(self) -> None:
        """Open issue with pull_request attribute is classified as 'pull_request'."""
        gh_issue = MagicMock()
        gh_issue.pull_request = MagicMock()
        gh_issue.state = "open"

        result = _classify_issue(gh_issue)

        assert result == ("pull_request", "open")

    def test_classify_closed_issue(self) -> None:
        """Closed issue is classified with state 'closed'."""
        gh_issue = MagicMock()
        gh_issue.pull_request = None
        gh_issue.state = "closed"

        result = _classify_issue(gh_issue)

        assert result == ("issue", "closed")

    def test_classify_merged_pr(self) -> None:
        """Closed PR with merged_at is classified as 'merged'."""
        gh_issue = MagicMock()
        gh_issue.pull_request = MagicMock()
        gh_issue.pull_request.merged_at = datetime(2024, 1, 1, tzinfo=UTC)
        gh_issue.state = "closed"

        result = _classify_issue(gh_issue)

        assert result == ("pull_request", "merged")


class TestComputeIssueHash:
    """Tests for _compute_issue_hash."""

    def test_same_content_same_hash(self) -> None:
        """Identical content produces identical hashes."""
        hash1 = _compute_issue_hash("title", "body", "open", ["bug"])
        hash2 = _compute_issue_hash("title", "body", "open", ["bug"])

        assert hash1 == hash2

    def test_different_content_different_hash(self) -> None:
        """Different content produces different hashes."""
        hash1 = _compute_issue_hash("title1", "body", "open", ["bug"])
        hash2 = _compute_issue_hash("title2", "body", "open", ["bug"])

        assert hash1 != hash2

    def test_hash_is_hex_string(self) -> None:
        """Hash is a 64-character hex string (SHA-256)."""
        result = _compute_issue_hash("title", "body", "open", [])

        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


class TestGitHubCollector:
    """Tests for GitHubCollector."""

    def test_init(self) -> None:
        """GitHubCollector initializes with a token and org."""
        collector = GitHubCollector(token="ghp_test", org="canonical")  # noqa: S106

        assert collector.org == "canonical"

    def test_is_maintainer(self) -> None:
        """is_maintainer checks against the maintainer list."""
        collector = GitHubCollector(
            token="ghp_test",
            org="canonical",
            maintainers=["mr-cal", "lengau"],
        )

        assert collector.is_maintainer("mr-cal") is True
        assert collector.is_maintainer("some-user") is False

    def test_is_maintainer_empty_list(self) -> None:
        """is_maintainer returns False when no maintainers configured."""
        collector = GitHubCollector(token="ghp_test", org="canonical")  # noqa: S106

        assert collector.is_maintainer("anyone") is False
