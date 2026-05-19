"""Tests for the snapshot generator."""


from craft_dashboard.collectors.snapshots import compute_snapshot_counts


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
