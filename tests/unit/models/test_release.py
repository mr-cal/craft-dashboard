"""Tests for the Release model."""

from craft_dashboard.models.release import Release


class TestReleaseModel:
    """Tests for the Release model."""

    def test_tablename(self) -> None:
        """Release model uses 'releases' table."""
        assert Release.__tablename__ == "releases"

    def test_required_columns(self) -> None:
        """Release model has all required columns."""
        column_names = {col.name for col in Release.__table__.columns}
        expected = {
            "id",
            "project_id",
            "version",
            "branch",
            "released_at",
            "is_hotfix",
            "metadata",
        }
        assert expected.issubset(column_names)

    def test_unique_constraint(self) -> None:
        """Release has a unique constraint on (project_id, branch)."""
        constraints = Release.__table__.constraints
        unique_constraints = [
            c
            for c in constraints
            if hasattr(c, "columns")
            and {col.name for col in c.columns} == {"project_id", "branch"}
        ]
        assert len(unique_constraints) == 1
