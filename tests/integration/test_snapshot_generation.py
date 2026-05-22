"""Integration tests for realistic snapshot generation scenarios."""

from datetime import UTC, date, datetime, timedelta

from craft_dashboard.collectors.snapshots import compute_snapshot_counts

_TODAY = date(2024, 6, 1)
_ZERO_COUNTS = {
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


def _make_issue(
    issue_type: str = "issue",
    state: str = "open",
    author: str = "user",
    *,
    is_bot: bool = False,
    labels: list[str] | None = None,
    age_days: int = 30,
    closed_days_ago: int | None = None,
    today: date = _TODAY,
) -> dict:
    today_dt = datetime.combine(today, datetime.min.time(), tzinfo=UTC)
    return {
        "issue_type": issue_type,
        "state": state,
        "author": author,
        "author_is_bot": is_bot,
        "labels": labels or [],
        "created_at": today_dt - timedelta(days=age_days),
        "closed_at": (
            today_dt - timedelta(days=closed_days_ago)
            if closed_days_ago is not None and state in ("closed", "merged")
            else None
        ),
    }


def _make_issues(specs: list[dict], today: date = _TODAY) -> list[dict]:
    return [_make_issue(today=today, **spec) for spec in specs]


class TestRealisticSnapshotScenarios:
    """End-to-end style tests for compute_snapshot_counts."""

    def test_typical_project(self) -> None:
        maintainers = {f"maintainer-{index}" for index in range(1, 6)}
        issues = _make_issues(
            [
                *[
                    {
                        "author": f"maintainer-{index}",
                        "age_days": age,
                        "labels": ["bug"] if index <= 2 else [],
                    }
                    for index, age in zip(
                        range(1, 6), [50, 60, 70, 80, 90], strict=True
                    )
                ],
                *[
                    {
                        "author": f"contributor-{index}",
                        "age_days": age,
                        "labels": ["bug"] if index <= 3 else [],
                    }
                    for index, age in zip(
                        range(1, 11),
                        [10, 20, 30, 40, 100, 110, 120, 130, 140, 150],
                        strict=True,
                    )
                ],
                {
                    "author": "renovate[bot]",
                    "is_bot": True,
                    "age_days": 5,
                    "labels": ["bug"],
                },
                {"author": "dependabot[bot]", "is_bot": True, "age_days": 15},
                {"author": "maintainer-1", "state": "closed", "closed_days_ago": 0},
                {
                    "author": "contributor-closed",
                    "state": "closed",
                    "closed_days_ago": 0,
                },
                {
                    "author": "release-bot[bot]",
                    "is_bot": True,
                    "state": "closed",
                    "closed_days_ago": 0,
                },
                *[
                    {
                        "author": f"old-closed-{index}",
                        "state": "closed",
                        "closed_days_ago": 1,
                    }
                    for index in range(5)
                ],
            ]
        )

        result = compute_snapshot_counts(issues, maintainers, today=_TODAY)

        assert result["open_issues"] == 17
        assert result["open_issues_internal"] == 5
        assert result["open_issues_external"] == 10
        assert result["open_issues_bots"] == 2
        assert result["open_prs"] == 0
        assert result["open_bugs"] == 6
        assert result["closed_issues"] == 3
        assert result["closed_issues_internal"] == 1
        assert result["closed_issues_external"] == 1
        assert result["closed_issues_bots"] == 1
        assert result["median_issue_age"] == 70
        assert result["median_issue_age_internal"] == 70
        assert result["nm_median_issue_age"] == 105
        assert result["median_issue_age_bots"] == 10

    def test_all_closed_project(self) -> None:
        issues = _make_issues(
            [
                {"author": "alice", "state": "closed", "closed_days_ago": 0},
                {"author": "bob", "state": "closed", "closed_days_ago": 0},
                {"author": "charlie", "state": "closed", "closed_days_ago": 1},
                {"author": "dana", "state": "closed", "closed_days_ago": 2},
            ]
        )

        result = compute_snapshot_counts(issues, {"alice"}, today=_TODAY)

        assert result["open_issues"] == 0
        assert result["open_issues_internal"] == 0
        assert result["open_issues_external"] == 0
        assert result["open_issues_bots"] == 0
        assert result["closed_issues"] == 2
        assert result["closed_issues_internal"] == 1
        assert result["closed_issues_external"] == 1
        assert result["median_issue_age"] == 0
        assert result["median_issue_age_internal"] == 0
        assert result["nm_median_issue_age"] == 0
        assert result["median_issue_age_bots"] == 0

    def test_bots_only_project(self) -> None:
        issues = _make_issues(
            [
                {"author": "renovate[bot]", "is_bot": True, "age_days": 7},
                {"author": "dependabot[bot]", "is_bot": True, "age_days": 14},
                {"author": "github-actions[bot]", "is_bot": True, "age_days": 21},
            ]
        )

        result = compute_snapshot_counts(issues, set(), today=_TODAY)

        assert result["open_issues"] == 3
        assert result["open_issues_internal"] == 0
        assert result["open_issues_external"] == 0
        assert result["open_issues_bots"] == 3
        assert result["median_issue_age_bots"] == 14
        assert result["median_issue_age"] == 14

    def test_mixed_prs_and_issues(self) -> None:
        maintainers = {"alice", "bob"}
        issues = _make_issues(
            [
                {"author": "alice", "age_days": 20},
                {"author": "external-issue", "age_days": 10},
                {
                    "issue_type": "pull_request",
                    "author": "bob",
                    "age_days": 8,
                },
                {
                    "issue_type": "pull_request",
                    "author": "merge-bot[bot]",
                    "is_bot": True,
                    "age_days": 4,
                },
                {"author": "issue-closer", "state": "closed", "closed_days_ago": 0},
                {
                    "issue_type": "pull_request",
                    "author": "contributor-pr",
                    "state": "merged",
                    "closed_days_ago": 0,
                },
            ]
        )

        result = compute_snapshot_counts(issues, maintainers, today=_TODAY)

        assert result["open_issues"] == 2
        assert result["open_issues_internal"] == 1
        assert result["open_issues_external"] == 1
        assert result["open_prs"] == 2
        assert result["open_prs_internal"] == 1
        assert result["open_prs_external"] == 0
        assert result["open_prs_bots"] == 1
        assert result["closed_issues"] == 1
        assert result["closed_prs"] == 1
        assert result["closed_prs_external"] == 1

    def test_bug_label_counting(self) -> None:
        issues = _make_issues(
            [
                {"author": "bug-reporter-1", "labels": ["bug"]},
                {"author": "bug-reporter-2", "labels": ["needs-triage", "bug"]},
                {"author": "feature-requester", "labels": ["enhancement"]},
                {
                    "author": "bug-closed",
                    "state": "closed",
                    "labels": ["bug"],
                    "closed_days_ago": 0,
                },
                {
                    "issue_type": "pull_request",
                    "author": "pr-author",
                    "labels": ["bug"],
                },
            ]
        )

        result = compute_snapshot_counts(issues, set(), today=_TODAY)

        assert result["open_issues"] == 3
        assert result["open_bugs"] == 2

    def test_median_age_calculation(self) -> None:
        issues = _make_issues(
            [
                {"author": "user-1", "age_days": 10},
                {"author": "user-2", "age_days": 20},
                {"author": "user-3", "age_days": 30},
            ]
        )

        result = compute_snapshot_counts(issues, set(), today=_TODAY)

        assert result["median_issue_age"] == 20

    def test_per_group_median_ages(self) -> None:
        issues = _make_issues(
            [
                {"author": "alice", "age_days": 100},
                {"author": "contributor", "age_days": 50},
                {"author": "renovate[bot]", "is_bot": True, "age_days": 10},
            ]
        )

        result = compute_snapshot_counts(issues, {"alice"}, today=_TODAY)

        assert result["median_issue_age_internal"] == 100
        assert result["nm_median_issue_age"] == 50
        assert result["median_issue_age_bots"] == 10
        assert result["median_issue_age"] == 50

    def test_empty_issues_list(self) -> None:
        result = compute_snapshot_counts([], set(), today=_TODAY)

        assert result == _ZERO_COUNTS

    def test_closed_only_today(self) -> None:
        issues = _make_issues(
            [
                {"author": "user-1", "state": "closed", "closed_days_ago": 0},
                {"author": "user-2", "state": "closed", "closed_days_ago": 0},
                {"author": "user-3", "state": "closed", "closed_days_ago": 1},
                {"author": "user-4", "state": "closed", "closed_days_ago": 1},
                {"author": "user-5", "state": "closed", "closed_days_ago": 1},
            ]
        )

        result = compute_snapshot_counts(issues, set(), today=_TODAY)

        assert result["closed_issues"] == 2

    def test_maintainer_bot_overlap(self) -> None:
        issues = _make_issues(
            [
                {"author": "release-bot", "is_bot": True, "age_days": 9},
                {"author": "community-user", "age_days": 30},
            ]
        )

        result = compute_snapshot_counts(issues, {"release-bot"}, today=_TODAY)

        assert result["open_issues"] == 2
        assert result["open_issues_internal"] == 1
        assert result["open_issues_bots"] == 1
        assert result["open_issues_external"] == 1
        assert result["median_issue_age_internal"] == 9
        assert result["median_issue_age_bots"] == 9
        assert result["nm_median_issue_age"] == 30
