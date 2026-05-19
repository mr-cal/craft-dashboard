"""Tests for the Dependency model."""

from craft_dashboard.models.dependency import Dependency


class TestDependencyModel:
    """Tests for the Dependency model."""

    def test_tablename(self) -> None:
        """Dependency model uses 'dependencies' table."""
        assert Dependency.__tablename__ == "dependencies"

    def test_required_columns(self) -> None:
        """Dependency model has all required columns."""
        column_names = {col.name for col in Dependency.__table__.columns}
        expected = {
            "id",
            "project_id",
            "branch",
            "dependency_name",
            "version_spec",
            "source_file",
            "fetched_at",
        }
        assert expected.issubset(column_names)

    def test_unique_constraint(self) -> None:
        """Dependency has a unique constraint on (project_id, branch, dependency_name)."""
        constraints = Dependency.__table__.constraints
        unique_constraints = [
            c
            for c in constraints
            if hasattr(c, "columns")
            and {col.name for col in c.columns}
            == {"project_id", "branch", "dependency_name"}
        ]
        assert len(unique_constraints) == 1
