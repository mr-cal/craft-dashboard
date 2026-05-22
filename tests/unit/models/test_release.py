"""Tests for the Release model."""

import ast
from pathlib import Path

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

    def test_is_hotfix_explicitly_set_not_nullable(self) -> None:
        """Release.is_hotfix should explicitly set nullable=False."""
        module_path = Path(__file__).resolve().parents[3] / "craft_dashboard/models/release.py"
        tree = ast.parse(module_path.read_text())
        release_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Release"
        )
        field = next(
            node
            for node in release_class.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "is_hotfix"
        )
        nullable_kw = next(
            (kw.value for kw in field.value.keywords if kw.arg == "nullable"),
            None,
        )

        assert isinstance(nullable_kw, ast.Constant)
        assert nullable_kw.value is False
