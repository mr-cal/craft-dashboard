"""Regression tests for backfill_snapshots.compute_snapshot_for_date."""

import importlib.util
import pathlib
from datetime import UTC, date, datetime, timedelta

import pytest
from craft_dashboard.models.issue import Issue

MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3] / "scripts" / "backfill_snapshots.py"
)
SPEC = importlib.util.spec_from_file_location("backfill_snapshots", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
backfill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backfill)

_SNAPSHOT_DATE = date(2024, 6, 15)


def _make_issue(
    issue_type: str = "issue",
    created_days_ago: int = 30,
    closed_days_ago: int | None = None,
    *,
    author_is_maintainer: bool = False,
    author_is_bot: bool = False,
    labels: list[str] | None = None,
) -> Issue:
    """Create a minimal Issue for compute_snapshot_for_date."""
    created_at = datetime.combine(
        _SNAPSHOT_DATE - timedelta(days=created_days_ago),
        datetime.min.time(),
        tzinfo=UTC,
    )
    closed_at = None
    if closed_days_ago is not None:
        closed_at = datetime.combine(
            _SNAPSHOT_DATE - timedelta(days=closed_days_ago),
            datetime.min.time(),
            tzinfo=UTC,
        )
    return Issue(
        project_id=1,
        source="github",
        external_id=f"test-{id(object())}",
        issue_type=issue_type,
        title="test",
        state="open" if closed_at is None else "closed",
        author="test-author",
        author_is_maintainer=author_is_maintainer,
        author_is_bot=author_is_bot,
        labels=labels or [],
        created_at=created_at,
        closed_at=closed_at,
        last_fetched_at=datetime.now(tz=UTC),
    )


class TestBotFieldsInSnapshot:
    """Regression: backfill must populate bot fields (was all zeros before fix)."""

    def test_bot_issue_counted_in_open_issues_bots(self) -> None:
        issues = [_make_issue(author_is_bot=True)]
        result = backfill.compute_snapshot_for_date(issues, _SNAPSHOT_DATE)
        assert result["open_issues_bots"] == 1

    def test_bot_pr_counted_in_open_prs_bots(self) -> None:
        issues = [_make_issue(issue_type="pull_request", author_is_bot=True)]
        result = backfill.compute_snapshot_for_date(issues, _SNAPSHOT_DATE)
        assert result["open_prs_bots"] == 1

    def test_non_bot_issue_not_in_bots(self) -> None:
        issues = [_make_issue(author_is_bot=False)]
        result = backfill.compute_snapshot_for_date(issues, _SNAPSHOT_DATE)
        assert result["open_issues_bots"] == 0

    def test_closed_bot_issue_counted(self) -> None:
        issues = [
            _make_issue(
                author_is_bot=True,
                created_days_ago=10,
                closed_days_ago=0,
            ),
        ]
        result = backfill.compute_snapshot_for_date(issues, _SNAPSHOT_DATE)
        assert result["closed_issues_bots"] == 1

    def test_closed_bot_pr_counted(self) -> None:
        issues = [
            _make_issue(
                issue_type="pull_request",
                author_is_bot=True,
                created_days_ago=10,
                closed_days_ago=0,
            ),
        ]
        result = backfill.compute_snapshot_for_date(issues, _SNAPSHOT_DATE)
        assert result["closed_prs_bots"] == 1

    def test_mixed_authors_correct_bot_counts(self) -> None:
        """Bot issues alongside maintainer and external issues."""
        issues = [
            _make_issue(author_is_bot=True),
            _make_issue(author_is_bot=True),
            _make_issue(author_is_maintainer=True),
            _make_issue(),  # external
        ]
        result = backfill.compute_snapshot_for_date(issues, _SNAPSHOT_DATE)
        assert result["open_issues_bots"] == 2
        assert result["open_issues_internal"] == 1
        assert result["open_issues_external"] == 1
        assert result["open_issues"] == 4

    def test_bot_not_double_counted_as_external(self) -> None:
        """Bot authors must NOT appear in external counts."""
        issues = [_make_issue(author_is_bot=True)]
        result = backfill.compute_snapshot_for_date(issues, _SNAPSHOT_DATE)
        assert result["open_issues_external"] == 0
        assert result["open_issues_bots"] == 1


class TestMedianAgeFields:
    """Regression: backfill must populate all median age fields."""

    def test_all_median_fields_present(self) -> None:
        issues = [_make_issue(created_days_ago=10)]
        result = backfill.compute_snapshot_for_date(issues, _SNAPSHOT_DATE)
        expected_fields = [
            "median_issue_age",
            "median_pr_age",
            "nm_median_issue_age",
            "nm_median_pr_age",
            "median_issue_age_internal",
            "median_pr_age_internal",
            "median_issue_age_bots",
            "median_pr_age_bots",
            "median_age",
            "nm_median_age",
            "median_age_internal",
            "median_age_bots",
        ]
        for field in expected_fields:
            assert field in result, f"Missing median field: {field}"

    def test_bot_median_age_computed(self) -> None:
        issues = [
            _make_issue(author_is_bot=True, created_days_ago=20),
            _make_issue(author_is_bot=True, created_days_ago=10),
        ]
        result = backfill.compute_snapshot_for_date(issues, _SNAPSHOT_DATE)
        assert result["median_issue_age_bots"] == 15
        assert result["median_age_bots"] == 15

    def test_internal_median_age_computed(self) -> None:
        issues = [_make_issue(author_is_maintainer=True, created_days_ago=30)]
        result = backfill.compute_snapshot_for_date(issues, _SNAPSHOT_DATE)
        assert result["median_issue_age_internal"] == 30
        assert result["median_age_internal"] == 30

    def test_external_median_age_computed(self) -> None:
        issues = [_make_issue(created_days_ago=50)]
        result = backfill.compute_snapshot_for_date(issues, _SNAPSHOT_DATE)
        assert result["nm_median_issue_age"] == 50
        assert result["nm_median_age"] == 50


class TestLabelsRegression:
    """Regression: labels are list[str], not dict."""

    def test_bug_label_detected_from_list(self) -> None:
        issues = [_make_issue(labels=["bug", "priority"])]
        result = backfill.compute_snapshot_for_date(issues, _SNAPSHOT_DATE)
        assert result["open_bugs"] == 1

    def test_no_bug_label(self) -> None:
        issues = [_make_issue(labels=["enhancement"])]
        result = backfill.compute_snapshot_for_date(issues, _SNAPSHOT_DATE)
        assert result["open_bugs"] == 0

    def test_empty_labels_list(self) -> None:
        issues = [_make_issue(labels=[])]
        result = backfill.compute_snapshot_for_date(issues, _SNAPSHOT_DATE)
        assert result["open_bugs"] == 0


class TestSnapshotCompleteness:
    """Ensure compute_snapshot_for_date returns all required fields."""

    @pytest.mark.parametrize(
        "field",
        [
            "open_issues",
            "open_prs",
            "open_issues_external",
            "open_issues_internal",
            "open_issues_bots",
            "open_prs_external",
            "open_prs_internal",
            "open_prs_bots",
            "open_bugs",
            "closed_issues",
            "closed_prs",
            "closed_issues_external",
            "closed_issues_internal",
            "closed_issues_bots",
            "closed_prs_external",
            "closed_prs_internal",
            "closed_prs_bots",
        ],
    )
    def test_field_present(self, field: str) -> None:
        result = backfill.compute_snapshot_for_date([], _SNAPSHOT_DATE)
        assert field in result, f"Missing field: {field}"
