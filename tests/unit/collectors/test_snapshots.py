"""Tests for the snapshot generator."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

from craft_dashboard.collectors.snapshots import (
    _filter_issues_for_date,
    _increment_counts,
    backfill_missing_snapshots,
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
    *,
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

    def test_null_labels_does_not_crash(self) -> None:
        """An issue with `labels: None` (e.g. a NULL DB column) is treated as no labels.

        Regression test: ``issue.get("labels", [])`` only falls back to `[]`
        when the key is *missing*, not when it's explicitly `None` — which
        previously raised "argument of type 'NoneType' is not iterable" and
        aborted the whole snapshot for the project.
        """
        issues = [
            {
                "issue_type": "issue",
                "state": "open",
                "author": "external-user",
                "labels": None,
            },
        ]

        result = compute_snapshot_counts(issues=issues, maintainers=set())

        assert result["open_issues"] == 1
        assert result["open_bugs"] == 0

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

    def test_bot_detection_from_config_bots_set(self) -> None:
        """Bot detection works when author is in config bots set."""
        issues = [
            {
                "issue_type": "issue",
                "state": "open",
                "author": "renovate",
                "author_is_bot": False,
                "labels": [],
                "created_at": datetime(2025, 1, 1, tzinfo=UTC),
                "closed_at": None,
            }
        ]
        counts = compute_snapshot_counts(issues, set(), bots={"renovate"})
        assert counts["open_issues_bots"] == 1

    def test_median_age_empty_returns_zero(self) -> None:
        """With no open issues, median ages should be 0."""
        counts = compute_snapshot_counts([], set())
        assert counts["median_issue_age"] == 0
        assert counts["median_pr_age"] == 0
        assert counts["median_age"] == 0

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

    def test_median_age_combines_issues_and_prs(self) -> None:
        """Combined median_age should combine issues and PRs using the median-date algorithm."""
        issues = [
            {
                **_issue("open", author="external-user", created_days_ago=0),
                "created_at": datetime(2024, 5, 31, 23, tzinfo=UTC),
            },
            {
                **_issue(
                    "open",
                    issue_type="pull_request",
                    author="maintainer",
                    created_days_ago=1,
                ),
                "created_at": datetime(2024, 5, 30, 1, tzinfo=UTC),
            },
        ]

        result = compute_snapshot_counts(
            issues=issues,
            maintainers={"maintainer"},
            today=_TODAY,
        )

        assert result["median_issue_age"] == 0
        assert result["median_pr_age"] == 1
        assert result["median_age"] == 1

    def test_median_age_per_group_with_mixed_items(self) -> None:
        """Combined per-group median ages should include both issues and PRs."""
        issues = [
            _issue("open", author="external-a", created_days_ago=30),
            _issue(
                "open",
                issue_type="pull_request",
                author="external-b",
                created_days_ago=10,
            ),
            _issue("open", author="maintainer", created_days_ago=40),
            _issue(
                "open",
                issue_type="pull_request",
                author="maintainer",
                created_days_ago=20,
            ),
            _issue("open", author="bot-a", author_is_bot=True, created_days_ago=8),
            _issue(
                "open",
                issue_type="pull_request",
                author="bot-b",
                author_is_bot=True,
                created_days_ago=2,
            ),
        ]

        result = compute_snapshot_counts(
            issues=issues,
            maintainers={"maintainer"},
            today=_TODAY,
        )

        assert result["median_age"] == 15
        assert result["nm_median_age"] == 20
        assert result["median_age_internal"] == 30
        assert result["median_age_bots"] == 5

    def test_closed_combines_issues_and_prs(self) -> None:
        """Closed counts should combine issue and PR closures on the snapshot day."""
        issues = [
            _issue("closed", author="external-user", closed_days_ago=0),
            _issue(
                "merged",
                issue_type="pull_request",
                author="maintainer",
                closed_days_ago=0,
            ),
            _issue("closed", author="external-user", closed_days_ago=1),
            _issue(
                "merged",
                issue_type="pull_request",
                author="maintainer",
                closed_days_ago=2,
            ),
        ]

        result = compute_snapshot_counts(
            issues=issues,
            maintainers={"maintainer"},
            today=_TODAY,
        )

        assert result["closed_issues"] == 1
        assert result["closed_prs"] == 1
        assert result["closed_issues"] + result["closed_prs"] == 2

    def test_all_combined_fields_present(self) -> None:
        """Combined trend fields should always be present in the result payload."""
        result = compute_snapshot_counts(issues=[], maintainers=set(), today=_TODAY)

        assert {
            "median_age",
            "nm_median_age",
            "median_age_internal",
            "median_age_bots",
            "open_issues_bots",
            "open_prs_bots",
            "closed_issues_bots",
            "closed_prs_bots",
        } <= result.keys()


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


class TestFilterIssuesForDate:
    """Tests for _filter_issues_for_date."""

    _DATE = date(2024, 6, 15)

    def test_open_issue_included_as_open(self) -> None:
        """Issues open on the target date are included with state='open'."""
        issue = _issue("open", created_days_ago=10, reference_date=self._DATE)
        result = _filter_issues_for_date([issue], self._DATE)
        assert len(result) == 1
        assert result[0]["state"] == "open"

    def test_closed_before_date_excluded(self) -> None:
        """Issues closed before the target date are excluded."""
        issue = _issue(
            "closed", created_days_ago=20, closed_days_ago=5, reference_date=self._DATE
        )
        result = _filter_issues_for_date([issue], self._DATE)
        assert result == []

    def test_closed_on_date_included_with_original_state(self) -> None:
        """Issues closed exactly on the target date are included with their original state."""
        issue = _issue(
            "closed", created_days_ago=10, closed_days_ago=0, reference_date=self._DATE
        )
        result = _filter_issues_for_date([issue], self._DATE)
        assert len(result) == 1
        assert result[0]["state"] == "closed"

    def test_merged_pr_closed_on_date_preserves_merged_state(self) -> None:
        """Merged PRs closed on the date keep 'merged' state."""
        issue = _issue(
            "merged",
            issue_type="pull_request",
            created_days_ago=10,
            closed_days_ago=0,
            reference_date=self._DATE,
        )
        result = _filter_issues_for_date([issue], self._DATE)
        assert len(result) == 1
        assert result[0]["state"] == "merged"

    def test_not_yet_created_excluded(self) -> None:
        """Issues created after the target date are excluded."""
        issue = _issue("open", created_days_ago=-5, reference_date=self._DATE)
        result = _filter_issues_for_date([issue], self._DATE)
        assert result == []

    def test_stale_open_state_overridden_for_already_closed_issue(self) -> None:
        """Closed after the snapshot date keeps the issue open for that day."""
        issue = _issue(
            "open", created_days_ago=10, closed_days_ago=-2, reference_date=self._DATE
        )
        result = _filter_issues_for_date([issue], self._DATE)
        assert len(result) == 1
        assert result[0]["state"] == "open"

    def test_stale_open_state_corrected_for_issue_already_closed(self) -> None:
        """Closed before the snapshot date excludes the issue for that day."""
        issue = _issue(
            "open", created_days_ago=20, closed_days_ago=5, reference_date=self._DATE
        )
        result = _filter_issues_for_date([issue], self._DATE)
        assert result == []

    def test_closed_with_no_closed_at_treated_as_open(self) -> None:
        """Issues with no close date are treated as open."""
        issue = {
            "issue_type": "issue",
            "state": "closed",
            "author": "user1",
            "author_is_bot": False,
            "labels": [],
            "created_at": datetime.combine(self._DATE, datetime.min.time(), tzinfo=UTC)
            - timedelta(days=10),
            "closed_at": None,
        }
        result = _filter_issues_for_date([issue], self._DATE)
        assert len(result) == 1
        assert result[0]["state"] == "open"

    def test_missing_created_at_excluded(self) -> None:
        """Issues without a created_at are excluded."""
        issue = {
            "issue_type": "issue",
            "state": "open",
            "author": "user1",
            "author_is_bot": False,
            "labels": [],
            "created_at": None,
            "closed_at": None,
        }
        result = _filter_issues_for_date([issue], self._DATE)
        assert result == []

    def test_multiple_issues_mixed(self) -> None:
        """Mix of open, pre-closed, same-day-closed, and not-yet-created issues."""
        issues = [
            _issue("open", created_days_ago=10, reference_date=self._DATE),
            _issue(
                "closed",
                created_days_ago=20,
                closed_days_ago=5,
                reference_date=self._DATE,
            ),
            _issue(
                "closed",
                created_days_ago=10,
                closed_days_ago=0,
                reference_date=self._DATE,
            ),
            _issue("open", created_days_ago=-3, reference_date=self._DATE),
        ]
        result = _filter_issues_for_date(issues, self._DATE)
        assert len(result) == 2
        states = {result_item["state"] for result_item in result}
        assert states == {"open", "closed"}


class TestBackfillMissingSnapshots:
    """Tests for backfill_missing_snapshots."""

    _DATE = date(2024, 6, 15)

    def _make_issue(
        self, created_days_ago: int, closed_days_ago: int | None = None
    ) -> dict:
        created_at = datetime.combine(
            self._DATE, datetime.min.time(), tzinfo=UTC
        ) - timedelta(days=created_days_ago)
        closed_at = None
        if closed_days_ago is not None:
            closed_at = datetime.combine(
                self._DATE, datetime.min.time(), tzinfo=UTC
            ) - timedelta(days=closed_days_ago)
        return {
            "issue_type": "issue",
            "state": "open" if closed_days_ago is None else "closed",
            "author": "user1",
            "author_is_bot": False,
            "labels": [],
            "created_at": created_at,
            "closed_at": closed_at,
        }

    async def test_no_backfill_without_previous_snapshot(self) -> None:
        """Returns 0 and does nothing when there's no previous snapshot."""
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)  # no previous snapshot

        filled = await backfill_missing_snapshots(1, [], session, set())
        assert filled == 0

    async def test_backfills_missing_dates(self) -> None:
        """Generates snapshots for each missing date between last snapshot and today."""
        today = date.today()
        last = today - timedelta(days=4)  # 3 missing dates: today-3, today-2, today-1

        # scalar calls: first returns last_snapshot, then 0 for each exists check
        scalar_returns = [last, 0, 0, 0]
        session = AsyncMock()
        session.scalar = AsyncMock(side_effect=scalar_returns)
        session.execute = AsyncMock()
        session.commit = AsyncMock()

        issues = [self._make_issue(30)]  # one open issue

        with patch("craft_dashboard.collectors.snapshots.date") as mock_date:
            mock_date.today.return_value = today
            filled = await backfill_missing_snapshots(1, issues, session, set())

        assert filled == 3
        assert session.commit.called
