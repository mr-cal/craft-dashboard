"""Tests for the snapshot generator."""

from datetime import UTC, date, datetime, timedelta

from craft_dashboard.collectors.snapshots import (
    _increment_counts,
    compute_snapshot_counts,
)

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
    created_at = datetime.combine(
        reference_date, datetime.min.time(), tzinfo=UTC
    ) - timedelta(days=created_days_ago)
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


class _NaiveClosedAtDateTime(datetime):
    def date(self):
        if self.tzinfo is None:
            msg = "closed_at should be normalized before reading its date"
            raise AssertionError(msg)
        return super().date()


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
            "nm_median_issue_age": 0,
            "nm_median_pr_age": 0,
            "median_issue_age_internal": 0,
            "median_pr_age_internal": 0,
            "median_issue_age_bots": 0,
            "median_pr_age_bots": 0,
            "median_age": 0,
            "nm_median_age": 0,
            "median_age_internal": 0,
            "median_age_bots": 0,
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

        result_today = compute_snapshot_counts(
            issues=issues, maintainers=set(), today=_TODAY
        )
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

    def test_bots_parameter_flags_configured_bot_accounts(self) -> None:
        """Configured bot accounts are counted as bots without author_is_bot."""
        issues = [
            _issue("open", author="Copilot", author_is_bot=False),
            _issue("open", author="human-user", author_is_bot=False),
        ]

        result = compute_snapshot_counts(
            issues=issues,
            maintainers=set(),
            today=_TODAY,
            bots={"Copilot", "dependabot[bot]", "renovate[bot]"},
        )

        assert result["open_issues_bots"] == 1
        assert result["open_issues_external"] == 1

    def test_copilot_in_bots_parameter_is_treated_as_bot(self) -> None:
        """Copilot is treated as a bot when listed in configured bots."""
        issues = [
            _issue(
                "open", issue_type="pull_request", author="Copilot", created_days_ago=8
            ),
        ]

        result = compute_snapshot_counts(
            issues=issues,
            maintainers=set(),
            today=_TODAY,
            bots={"Copilot"},
        )

        assert result["open_prs_bots"] == 1
        assert result["open_prs_external"] == 0
        assert result["median_pr_age_bots"] == 8

    def test_bots_parameter_works_alongside_author_is_bot_field(self) -> None:
        """Configured bots and stored author_is_bot values are both honored."""
        issues = [
            _issue("open", author="Copilot", author_is_bot=False, created_days_ago=10),
            _issue(
                "open",
                author="dependabot[bot]",
                author_is_bot=True,
                created_days_ago=20,
            ),
            _issue(
                "open", author="human-user", author_is_bot=False, created_days_ago=30
            ),
        ]

        result = compute_snapshot_counts(
            issues=issues,
            maintainers=set(),
            today=_TODAY,
            bots={"Copilot"},
        )

        assert result["open_issues_bots"] == 2
        assert result["open_issues_external"] == 1
        assert result["median_issue_age_bots"] == 15

    def test_bot_open_pr_counted_in_bots(self) -> None:
        """Open PR from a bot author is counted in open_prs_bots."""
        issues = [
            _issue(
                "open",
                issue_type="pull_request",
                author="dependabot[bot]",
                author_is_bot=True,
            ),
            _issue(
                "open",
                issue_type="pull_request",
                author="human-user",
                author_is_bot=False,
            ),
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
            {
                "issue_type": "issue",
                "state": "open",
                "author": "lp-user",
                "labels": [],
                "created_at": None,
                "closed_at": None,
            },
            {
                "issue_type": "issue",
                "state": "open",
                "author": "another-lp-user",
                "labels": [],
                "created_at": None,
                "closed_at": None,
            },
        ]

        result = compute_snapshot_counts(
            issues=lp_issues, maintainers=set(), today=_TODAY
        )

        assert result["open_issues_bots"] == 0
        assert result["open_issues"] == 2

    def test_bot_open_issue_not_in_external(self) -> None:
        """Bot-authored open issue increments only open_issues_bots, not open_issues_external."""
        issues = [
            _issue("open", author="renovate[bot]", author_is_bot=True),
        ]

        result = compute_snapshot_counts(issues=issues, maintainers=set(), today=_TODAY)

        assert result["open_issues_bots"] == 1
        assert result["open_issues_external"] == 0

    def test_bot_closed_issue_counted_in_closed_bots(self) -> None:
        """Issue closed today by a bot is counted in closed_issues_bots."""
        issues = [
            _issue(
                "closed", author="renovate[bot]", author_is_bot=True, closed_days_ago=0
            ),
            _issue("closed", author="human", author_is_bot=False, closed_days_ago=0),
        ]

        result = compute_snapshot_counts(issues=issues, maintainers=set(), today=_TODAY)

        assert result["closed_issues_bots"] == 1
        assert result["closed_issues"] == 2

    def test_median_age_internal_external_bots(self) -> None:
        """Verify per-group median ages are computed separately."""
        maintainers = {"alice"}
        issues = [
            _issue(
                "open", author="alice", author_is_bot=False, created_days_ago=10
            ),  # internal
            _issue(
                "open", author="alice", author_is_bot=False, created_days_ago=30
            ),  # internal
            _issue(
                "open", author="bob", author_is_bot=False, created_days_ago=50
            ),  # external
            _issue(
                "open", author="carol", author_is_bot=False, created_days_ago=70
            ),  # external
            _issue(
                "open", author="bot1", author_is_bot=True, created_days_ago=5
            ),  # bot
            _issue(
                "open", author="bot2", author_is_bot=True, created_days_ago=15
            ),  # bot
        ]

        result = compute_snapshot_counts(
            issues=issues, maintainers=maintainers, today=_TODAY
        )

        # All ages: 10, 30, 50, 70, 5, 15 -> median of sorted [5,10,15,30,50,70] = (15+30)/2 = 22
        assert result["median_issue_age"] == 22
        # Internal: 10, 30 -> median = 20
        assert result["median_issue_age_internal"] == 20
        # External (non-maintainer, non-bot): 50, 70 -> median = 60
        assert result["nm_median_issue_age"] == 60
        # Bots: 5, 15 -> median = 10
        assert result["median_issue_age_bots"] == 10

    def test_median_age_no_bots_zero(self) -> None:
        """When there are no bot issues, median_issue_age_bots should be 0."""
        issues = [
            _issue("open", author="alice", author_is_bot=False, created_days_ago=10),
            _issue("open", author="bob", author_is_bot=False, created_days_ago=20),
        ]

        result = compute_snapshot_counts(issues=issues, maintainers=set(), today=_TODAY)

        assert result["median_issue_age_bots"] == 0

    def test_median_age_per_group_prs(self) -> None:
        """Verify per-group median ages work for PRs too."""
        maintainers = {"maintainer1"}
        issues = [
            _issue(
                "open",
                issue_type="pull_request",
                author="maintainer1",
                created_days_ago=20,
            ),
            _issue(
                "open",
                issue_type="pull_request",
                author="external1",
                created_days_ago=40,
            ),
            _issue(
                "open",
                issue_type="pull_request",
                author="bot1",
                author_is_bot=True,
                created_days_ago=8,
            ),
        ]

        result = compute_snapshot_counts(
            issues=issues, maintainers=maintainers, today=_TODAY
        )

        assert result["median_pr_age_internal"] == 20
        assert result["nm_median_pr_age"] == 40
        assert result["median_pr_age_bots"] == 8

    def test_combined_median_age_tracks_issues_and_prs_together(self) -> None:
        """Combined median age fields should include both issues and PRs."""
        maintainers = {"maintainer"}
        issues = [
            _issue("open", author="maintainer", created_days_ago=10),
            _issue("open", author="external-user", created_days_ago=20),
            _issue(
                "open",
                issue_type="pull_request",
                author="maintainer",
                created_days_ago=30,
            ),
            _issue(
                "open",
                issue_type="pull_request",
                author="bot-user",
                author_is_bot=True,
                created_days_ago=40,
            ),
        ]

        result = compute_snapshot_counts(
            issues=issues, maintainers=maintainers, today=_TODAY
        )

        assert result["median_age"] == 25
        assert result["median_age_internal"] == 20
        assert result["nm_median_age"] == 20
        assert result["median_age_bots"] == 40


class TestSnapshotNaiveClosedAt:
    def test_naive_closed_at_counted_correctly(self) -> None:
        today = date(2024, 6, 1)
        issues = [
            {
                "issue_type": "issue",
                "state": "closed",
                "author": "user",
                "author_is_bot": False,
                "labels": [],
                "created_at": datetime(2024, 1, 1, tzinfo=UTC),
                "closed_at": datetime(2024, 6, 1),
            }
        ]

        result = compute_snapshot_counts(issues, maintainers=set(), today=today)

        assert result["closed_issues"] == 1

    def test_naive_closed_at_is_normalized_before_date_check(self) -> None:
        today = date(2024, 6, 1)
        issues = [
            {
                "issue_type": "issue",
                "state": "closed",
                "author": "user",
                "author_is_bot": False,
                "labels": [],
                "created_at": datetime(2024, 1, 1, tzinfo=UTC),
                "closed_at": _NaiveClosedAtDateTime(2024, 6, 1),
            }
        ]

        result = compute_snapshot_counts(issues, maintainers=set(), today=today)

        assert result["closed_issues"] == 1


class TestIncrementCounts:
    def test_counts_internal_external_and_bot_roles(self) -> None:
        counts = {
            "open_issues": 0,
            "open_issues_internal": 0,
            "open_issues_external": 0,
            "open_issues_bots": 0,
            "closed_prs": 0,
            "closed_prs_internal": 0,
            "closed_prs_external": 0,
            "closed_prs_bots": 0,
        }

        _increment_counts(
            counts, issue_type="issue", is_internal=True, is_bot=False, prefix="open"
        )
        _increment_counts(
            counts,
            issue_type="pull_request",
            is_internal=False,
            is_bot=False,
            prefix="closed",
        )
        _increment_counts(
            counts,
            issue_type="pull_request",
            is_internal=False,
            is_bot=True,
            prefix="closed",
        )

        assert counts == {
            "open_issues": 1,
            "open_issues_internal": 1,
            "open_issues_external": 0,
            "open_issues_bots": 0,
            "closed_prs": 2,
            "closed_prs_internal": 0,
            "closed_prs_external": 1,
            "closed_prs_bots": 1,
        }

    def test_compute_snapshot_counts_with_mixed_open_and_closed_items(self) -> None:
        maintainers = {"maintainer"}
        issues = [
            _issue("open", author="external", author_is_bot=False),
            _issue(
                "open",
                issue_type="pull_request",
                author="maintainer",
                author_is_bot=False,
            ),
            _issue(
                "closed", author="maintainer", author_is_bot=False, closed_days_ago=0
            ),
            _issue(
                "merged",
                issue_type="pull_request",
                author="renovate[bot]",
                author_is_bot=True,
                closed_days_ago=0,
            ),
        ]

        result = compute_snapshot_counts(
            issues=issues, maintainers=maintainers, today=_TODAY
        )

        assert result["open_issues"] == 1
        assert result["open_issues_external"] == 1
        assert result["open_prs"] == 1
        assert result["open_prs_internal"] == 1
        assert result["closed_issues"] == 1
        assert result["closed_issues_internal"] == 1
        assert result["closed_prs"] == 1
        assert result["closed_prs_bots"] == 1
        assert result["closed_prs_external"] == 0
