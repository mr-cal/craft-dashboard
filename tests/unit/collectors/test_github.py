"""Tests for the GitHub data collector."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from craft_dashboard.collectors.github import (
    GitHubCollector,
    _classify_issue,
    _compute_issue_hash,
    _fetch_issue_comments,
    _fetch_pr_details,
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


class TestFetchIssueComments:
    """Tests for _fetch_issue_comments."""

    def test_returns_last_10_comments(self) -> None:
        """Returns at most 10 comments, most recent last."""
        mock_comment = MagicMock()
        mock_comment.user.login = "alice"
        mock_comment.body = "looks good"
        mock_comment.created_at = datetime(2024, 3, 1, tzinfo=UTC)

        gh_issue = MagicMock()
        gh_issue.get_comments.return_value = [mock_comment] * 15

        result = _fetch_issue_comments(gh_issue)

        assert len(result) == 10
        assert result[0]["author"] == "alice"
        assert result[0]["body"] == "looks good"
        assert result[0]["created_at"] == "2024-03-01T00:00:00+00:00"
        assert result[0]["type"] == "comment"

    def test_truncates_long_body(self) -> None:
        """Comment bodies are truncated to 1000 chars."""
        mock_comment = MagicMock()
        mock_comment.user.login = "bob"
        mock_comment.body = "x" * 2000
        mock_comment.created_at = datetime(2024, 3, 1, tzinfo=UTC)

        gh_issue = MagicMock()
        gh_issue.get_comments.return_value = [mock_comment]

        result = _fetch_issue_comments(gh_issue)

        assert len(result[0]["body"]) == 1000

    def test_handles_no_user(self) -> None:
        """Comments with no user (deleted accounts) get author 'unknown'."""
        mock_comment = MagicMock()
        mock_comment.user = None
        mock_comment.body = "hello"
        mock_comment.created_at = datetime(2024, 3, 1, tzinfo=UTC)

        gh_issue = MagicMock()
        gh_issue.get_comments.return_value = [mock_comment]

        result = _fetch_issue_comments(gh_issue)

        assert result[0]["author"] == "unknown"

    def test_empty_comments(self) -> None:
        """Returns empty list when there are no comments."""
        gh_issue = MagicMock()
        gh_issue.get_comments.return_value = []

        result = _fetch_issue_comments(gh_issue)

        assert result == []


class TestFetchPRDetails:
    """Tests for _fetch_pr_details."""

    def test_approved_pr(self) -> None:
        """Returns review_status='approved' when latest unique reviewer approved."""
        mock_review = MagicMock()
        mock_review.user.login = "reviewer1"
        mock_review.state = "APPROVED"

        last_commit = MagicMock()
        last_commit.get_check_runs.return_value = []

        mock_pr = MagicMock()
        mock_pr.get_reviews.return_value = [mock_review]
        mock_pr.get_review_comments.return_value = []
        mock_pr.get_commits.return_value = [last_commit]
        mock_pr.additions = 50
        mock_pr.deletions = 20
        mock_pr.changed_files = 3

        result = _fetch_pr_details(mock_pr)

        assert result["review_status"] == "approved"
        assert result["review_count"] == 1
        assert result["diff_additions"] == 50
        assert result["diff_deletions"] == 20
        assert result["diff_files_changed"] == 3

    def test_changes_requested(self) -> None:
        """Returns review_status='changes_requested' when any reviewer requested changes."""
        reviews = []
        for state in ["APPROVED", "CHANGES_REQUESTED"]:
            r = MagicMock()
            r.user.login = f"reviewer_{state}"
            r.state = state
            reviews.append(r)

        mock_pr = MagicMock()
        mock_pr.get_reviews.return_value = reviews
        mock_pr.get_review_comments.return_value = []
        mock_pr.get_commits.return_value = []
        mock_pr.additions = 10
        mock_pr.deletions = 5
        mock_pr.changed_files = 2

        result = _fetch_pr_details(mock_pr)

        assert result["review_status"] == "changes_requested"

    def test_ci_checks_classified(self) -> None:
        """CI checks are split into passing, failing, pending lists."""
        passing_check = MagicMock()
        passing_check.name = "lint"
        passing_check.conclusion = "success"

        failing_check = MagicMock()
        failing_check.name = "integration-tests"
        failing_check.conclusion = "failure"

        pending_check = MagicMock()
        pending_check.name = "build"
        pending_check.conclusion = None  # still running

        last_commit = MagicMock()
        last_commit.get_check_runs.return_value = [
            passing_check, failing_check, pending_check
        ]

        commits_list = [last_commit]

        mock_pr = MagicMock()
        mock_pr.get_reviews.return_value = []
        mock_pr.get_review_comments.return_value = []
        mock_pr.get_commits.return_value = commits_list
        mock_pr.additions = 0
        mock_pr.deletions = 0
        mock_pr.changed_files = 0

        result = _fetch_pr_details(mock_pr)

        assert "lint" in result["ci_passing"]
        assert "integration-tests" in result["ci_failing"]
        assert "build" in result["ci_pending"]

    def test_unresolved_review_comments_counted(self) -> None:
        """Counts review comments that are not resolved."""
        resolved = MagicMock()
        resolved.position = None  # resolved comments have position=None

        unresolved = MagicMock()
        unresolved.position = 5  # unresolved comments have a line position

        mock_pr = MagicMock()
        mock_pr.get_reviews.return_value = []
        mock_pr.get_review_comments.return_value = [resolved, unresolved]
        mock_pr.get_commits.return_value = []
        mock_pr.additions = 0
        mock_pr.deletions = 0
        mock_pr.changed_files = 0

        result = _fetch_pr_details(mock_pr)

        assert result["unresolved_review_comments"] == 1

