"""Tests for the snapshot generator."""

from datetime import UTC, date, datetime, timedelta

from craft_dashboard.collectors.snapshots import compute_snapshot_counts

_TODAY = date(2024, 6, 1)


def _issue(
    state,
    issue_type="issue",
    created_days_ago=100,
    closed_days_ago=None,
    author="user1",
    labels=None,
    reference_date=_TODAY,
):
    created_at = datetime.combine(reference_date, datetime.min.time(), tzinfo=UTC) - timedelta(
        days=created_days_ago
    )
    closed_at = None
    if closed_days_ago is not None:
        closed_at = datetime.combine(
            reference_date, datetime.min.time(), tzinfo=UTC
        ) - timedelta(days=closed_days_ago)
    return {
        "issue_type": issue_type,
        "state": state,
        "author": author,
        "labels": labels or [],
        "created_at": created_at,
        "closed_at": closed_at,
    }


class TestComputeSnapshotCounts:
    """Tests for compute_snapshot_counts."""

    def test_empty_issues(self) -> None:
        """No issues produces all-zero counts."""
        result = compute_snapshot_counts(issues=[], maintainers=set())

        assert result == {
            "open_issues": 0,
            "open_prs": 0,
            "open_issues_external": 0,
            "open_issues_internal": 0,
            "open_prs_external": 0,
            "open_prs_internal": 0,
            "open_bugs": 0,
            "median_issue_age": 0,
            "median_pr_age": 0,
            "closed_issues": 0,
            "closed_prs": 0,
            "closed_issues_external": 0,
            "closed_issues_internal": 0,
            "closed_prs_external": 0,
            "closed_prs_internal": 0,
        }

    def test_counts_open_issues(self) -> None:
        """Counts open issues correctly."""
        issues = [
            {
                "issue_type": "issue",
                "state": "open",
                "author": "external-user",
                "labels": [],
            },
            {
                "issue_type": "issue",
                "state": "open",
                "author": "mr-cal",
                "labels": ["bug"],
            },
            {
                "issue_type": "issue",
                "state": "closed",
                "author": "someone",
                "labels": [],
            },
        ]
        maintainers = {"mr-cal"}

        result = compute_snapshot_counts(issues=issues, maintainers=maintainers)

        assert result["open_issues"] == 2
        assert result["open_issues_external"] == 1
        assert result["open_issues_internal"] == 1
        assert result["open_bugs"] == 1

    def test_counts_open_prs(self) -> None:
        """Counts open PRs correctly."""
        issues = [
            {
                "issue_type": "pull_request",
                "state": "open",
                "author": "external-user",
                "labels": [],
            },
            {
                "issue_type": "pull_request",
                "state": "open",
                "author": "mr-cal",
                "labels": [],
            },
            {
                "issue_type": "pull_request",
                "state": "merged",
                "author": "someone",
                "labels": [],
            },
        ]
        maintainers = {"mr-cal"}

        result = compute_snapshot_counts(issues=issues, maintainers=maintainers)

        assert result["open_prs"] == 2
        assert result["open_prs_external"] == 1
        assert result["open_prs_internal"] == 1

    def test_median_age_single_issue(self) -> None:
        """One open issue created 100 days ago -> median_issue_age == 100."""
        issues = [_issue("open", created_days_ago=100)]

        result = compute_snapshot_counts(issues=issues, maintainers=set(), today=_TODAY)

        assert result["median_issue_age"] == 100

    def test_median_age_multiple_issues(self) -> None:
        """Three open issues with ages 10, 20, 30 days -> median == 20."""
        issues = [
            _issue("open", created_days_ago=10),
            _issue("open", created_days_ago=20),
            _issue("open", created_days_ago=30),
        ]

        result = compute_snapshot_counts(issues=issues, maintainers=set(), today=_TODAY)

        assert result["median_issue_age"] == 20

    def test_median_age_no_open_issues(self) -> None:
        """Only closed issues -> median_issue_age == 0."""
        issues = [_issue("closed", created_days_ago=50, closed_days_ago=0)]

        result = compute_snapshot_counts(issues=issues, maintainers=set(), today=_TODAY)

        assert result["median_issue_age"] == 0

    def test_closed_issues_counts_today_only(self) -> None:
        """Issue closed today -> closed_issues == 1."""
        issues = [_issue("closed", closed_days_ago=0)]

        result = compute_snapshot_counts(issues=issues, maintainers=set(), today=_TODAY)

        assert result["closed_issues"] == 1

    def test_closed_issues_excludes_yesterday(self) -> None:
        """Issue closed yesterday -> closed_issues == 0 (no double-counting)."""
        issues = [_issue("closed", closed_days_ago=1)]

        result = compute_snapshot_counts(issues=issues, maintainers=set(), today=_TODAY)

        assert result["closed_issues"] == 0

    def test_no_double_counting(self) -> None:
        """Issue closed on a day is counted only in that day's snapshot."""
        yesterday = date(2024, 5, 31)
        # Issue closed on 2024-05-31 (= yesterday relative to _TODAY)
        issues = [_issue("closed", closed_days_ago=0, reference_date=yesterday)]

        result_today = compute_snapshot_counts(issues=issues, maintainers=set(), today=_TODAY)
        result_yesterday = compute_snapshot_counts(
            issues=issues, maintainers=set(), today=yesterday
        )

        assert result_today["closed_issues"] == 0
        assert result_yesterday["closed_issues"] == 1

    def test_closed_issues_exact_day(self) -> None:
        """Only issues closed on the exact snapshot day are counted."""
        issues = [
            _issue("closed", closed_days_ago=0),  # today -> counted
            _issue("closed", closed_days_ago=1),  # yesterday -> not counted
            _issue("closed", closed_days_ago=2),  # 2 days ago -> not counted
        ]

        result = compute_snapshot_counts(issues=issues, maintainers=set(), today=_TODAY)

        assert result["closed_issues"] == 1
