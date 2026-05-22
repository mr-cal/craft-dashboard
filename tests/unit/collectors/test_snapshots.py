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
    author_is_bot=False,
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
        "author_is_bot": author_is_bot,
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
            "open_issues_bots": 0,
            "open_prs_bots": 0,
            "open_bugs": 0,
            "median_issue_age": 0,
            "median_pr_age": 0,
            "closed_issues": 0,
            "closed_prs": 0,
            "closed_issues_external": 0,
            "closed_issues_internal": 0,
            "closed_prs_external": 0,
            "closed_prs_internal": 0,
            "closed_issues_bots": 0,
            "closed_prs_bots": 0,
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

    def test_bot_open_issue_counted_in_bots(self) -> None:
        """Open issue from a bot author is counted in open_issues_bots."""
        issues = [
            _issue("open", author="renovate[bot]", author_is_bot=True),
            _issue("open", author="human-user", author_is_bot=False),
        ]

        result = compute_snapshot_counts(issues=issues, maintainers=set(), today=_TODAY)

        assert result["open_issues_bots"] == 1
        assert result["open_issues"] == 2

    def test_bot_open_pr_counted_in_bots(self) -> None:
        """Open PR from a bot author is counted in open_prs_bots."""
        issues = [
            _issue("open", issue_type="pull_request", author="dependabot[bot]", author_is_bot=True),
            _issue("open", issue_type="pull_request", author="human-user", author_is_bot=False),
        ]

        result = compute_snapshot_counts(issues=issues, maintainers=set(), today=_TODAY)

        assert result["open_prs_bots"] == 1
        assert result["open_prs"] == 2

    def test_non_bot_issues_not_in_bots_count(self) -> None:
        """Human-authored issues do not appear in open_issues_bots."""
        issues = [
            _issue("open", author="alice", author_is_bot=False),
            _issue("open", author="bob", author_is_bot=False),
        ]

        result = compute_snapshot_counts(issues=issues, maintainers=set(), today=_TODAY)

        assert result["open_issues_bots"] == 0

    def test_lp_style_issues_have_zero_bots(self) -> None:
        """LP-style issues (no author_is_bot field) default to 0 bots."""
        lp_issues = [
            {"issue_type": "issue", "state": "open", "author": "lp-user", "labels": [],
             "created_at": None, "closed_at": None},
            {"issue_type": "issue", "state": "open", "author": "another-lp-user", "labels": [],
             "created_at": None, "closed_at": None},
        ]

        result = compute_snapshot_counts(issues=lp_issues, maintainers=set(), today=_TODAY)

        assert result["open_issues_bots"] == 0
        assert result["open_issues"] == 2

    def test_bot_closed_issue_counted_in_closed_bots(self) -> None:
        """Issue closed today by a bot is counted in closed_issues_bots."""
        issues = [
            _issue("closed", author="renovate[bot]", author_is_bot=True, closed_days_ago=0),
            _issue("closed", author="human", author_is_bot=False, closed_days_ago=0),
        ]

        result = compute_snapshot_counts(issues=issues, maintainers=set(), today=_TODAY)

        assert result["closed_issues_bots"] == 1
        assert result["closed_issues"] == 2
