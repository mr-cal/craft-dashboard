"""Additional edge-case tests for GitHub collector helpers."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from craft_dashboard.collectors.github import (
    _classify_issue,
    _compute_issue_hash,
    _fetch_pr_details,
)


def _make_review(login: str | None, state: str) -> MagicMock:
    review = MagicMock()
    review.state = state
    review.user = None if login is None else MagicMock(login=login)
    return review


def _make_pr(
    reviews: list[MagicMock] | None = None,
    comments: list[MagicMock] | None = None,
) -> MagicMock:
    last_commit = MagicMock()
    last_commit.get_check_runs.return_value = []

    pr = MagicMock()
    pr.get_reviews.return_value = reviews or []
    pr.get_review_comments.return_value = comments or []
    pr.get_commits.return_value = [last_commit]
    pr.additions = 12
    pr.deletions = 4
    pr.changed_files = 2
    return pr


class TestClassifyIssueEdgeCases:
    def test_open_issue(self) -> None:
        gh_issue = MagicMock()
        gh_issue.pull_request = None
        gh_issue.state = "open"

        assert _classify_issue(gh_issue) == ("issue", "open")

    def test_closed_issue(self) -> None:
        gh_issue = MagicMock()
        gh_issue.pull_request = None
        gh_issue.state = "closed"

        assert _classify_issue(gh_issue) == ("issue", "closed")

    def test_open_pr(self) -> None:
        gh_issue = MagicMock()
        gh_issue.pull_request = MagicMock()
        gh_issue.state = "open"

        assert _classify_issue(gh_issue) == ("pull_request", "open")

    def test_closed_pr_merged(self) -> None:
        gh_issue = MagicMock()
        gh_issue.pull_request = MagicMock()
        gh_issue.pull_request.merged_at = datetime(2024, 1, 1, tzinfo=UTC)
        gh_issue.state = "closed"

        assert _classify_issue(gh_issue) == ("pull_request", "merged")

    def test_closed_pr_not_merged(self) -> None:
        gh_issue = MagicMock()
        gh_issue.pull_request = MagicMock()
        gh_issue.pull_request.merged_at = None
        gh_issue.state = "closed"

        assert _classify_issue(gh_issue) == ("pull_request", "closed")


class TestComputeIssueHashEdgeCases:
    def test_none_body_matches_empty_body_hash(self) -> None:
        assert _compute_issue_hash(
            "title", None, "open", ["bug"]
        ) == _compute_issue_hash("title", "", "open", ["bug"])

    def test_label_order_does_not_change_hash(self) -> None:
        assert _compute_issue_hash(
            "title", "body", "open", ["zeta", "alpha"]
        ) == _compute_issue_hash("title", "body", "open", ["alpha", "zeta"])

    def test_different_state_changes_hash(self) -> None:
        assert _compute_issue_hash(
            "title", "body", "open", ["bug"]
        ) != _compute_issue_hash("title", "body", "closed", ["bug"])

    def test_same_inputs_are_deterministic(self) -> None:
        hash1 = _compute_issue_hash("title", "body", "open", ["bug", "triaged"])
        hash2 = _compute_issue_hash("title", "body", "open", ["bug", "triaged"])

        assert hash1 == hash2


class TestFetchPrDetailsReviewLogic:
    def test_all_approved(self) -> None:
        pr = _make_pr(
            reviews=[_make_review("alice", "APPROVED"), _make_review("bob", "APPROVED")]
        )

        result = _fetch_pr_details(pr)

        assert result["review_status"] == "approved"
        assert result["review_count"] == 2

    def test_changes_requested_overrides_approval(self) -> None:
        pr = _make_pr(
            reviews=[
                _make_review("alice", "APPROVED"),
                _make_review("bob", "CHANGES_REQUESTED"),
            ]
        )

        result = _fetch_pr_details(pr)

        assert result["review_status"] == "changes_requested"
        assert result["review_count"] == 2

    def test_latest_review_per_reviewer_wins(self) -> None:
        pr = _make_pr(
            reviews=[
                _make_review("alice", "CHANGES_REQUESTED"),
                _make_review("alice", "APPROVED"),
                _make_review("bob", "APPROVED"),
            ]
        )

        result = _fetch_pr_details(pr)

        assert result["review_status"] == "approved"
        assert result["review_count"] == 2

    def test_commented_reviews_are_ignored(self) -> None:
        pr = _make_pr(reviews=[_make_review("alice", "COMMENTED")])

        result = _fetch_pr_details(pr)

        assert result["review_status"] == "pending"
        assert result["review_count"] == 0

    def test_dismissed_reviews_are_ignored(self) -> None:
        pr = _make_pr(reviews=[_make_review("alice", "DISMISSED")])

        result = _fetch_pr_details(pr)

        assert result["review_status"] == "pending"
        assert result["review_count"] == 0

    def test_no_reviews_is_pending(self) -> None:
        pr = _make_pr(reviews=[])

        result = _fetch_pr_details(pr)

        assert result["review_status"] == "pending"
        assert result["review_count"] == 0

    def test_unresolved_comments_counted(self) -> None:
        resolved = MagicMock(position=None)
        unresolved_one = MagicMock(position=3)
        unresolved_two = MagicMock(position=9)
        pr = _make_pr(comments=[resolved, unresolved_one, unresolved_two])

        result = _fetch_pr_details(pr)

        assert result["unresolved_review_comments"] == 2
