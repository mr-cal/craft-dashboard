"""Tests for the Snapshot model."""

from datetime import date

from craft_dashboard.models.snapshot import Snapshot


class TestSnapshotModel:
    """Tests for the Snapshot model."""

    def test_tablename(self) -> None:
        """Snapshot model uses 'snapshots' table."""
        assert Snapshot.__tablename__ == "snapshots"

    def test_required_columns(self) -> None:
        """Snapshot model has all required columns."""
        column_names = {col.name for col in Snapshot.__table__.columns}
        expected = {
            "id",
            "project_id",
            "snapshot_date",
            "open_issues",
            "open_prs",
            "open_issues_external",
            "open_issues_internal",
            "open_prs_external",
            "open_prs_internal",
            "open_bugs",
            "median_age",
            "nm_median_age",
            "median_age_internal",
            "median_age_bots",
        }
        assert expected.issubset(column_names)

    def test_unique_constraint(self) -> None:
        """Snapshot has a unique constraint on (project_id, snapshot_date)."""
        constraints = Snapshot.__table__.constraints
        unique_constraints = [
            c
            for c in constraints
            if hasattr(c, "columns")
            and {col.name for col in c.columns} == {"project_id", "snapshot_date"}
        ]
        assert len(unique_constraints) == 1

    def test_external_aliases_return_non_maintainer_values(self) -> None:
        """External median age aliases should mirror the non-maintainer fields."""
        snapshot = Snapshot(
            project_id=1,
            snapshot_date=date(2024, 1, 1),
            nm_median_issue_age=12,
            nm_median_pr_age=7,
        )

        assert snapshot.median_issue_age_external == snapshot.nm_median_issue_age
        assert snapshot.median_pr_age_external == snapshot.nm_median_pr_age
