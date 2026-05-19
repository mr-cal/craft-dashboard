"""Tests for the Snapshot model."""

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
