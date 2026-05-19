"""Tests for the Project model."""

from craft_dashboard.models.project import Project


class TestProjectModel:
    """Tests for the Project model."""

    def test_tablename(self) -> None:
        """Project model uses 'projects' table."""
        assert Project.__tablename__ == "projects"

    def test_required_columns(self) -> None:
        """Project model has all required columns."""
        column_names = {col.name for col in Project.__table__.columns}
        expected = {
            "id",
            "name",
            "category",
            "github_org",
            "launchpad_name",
            "display_order",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(column_names)

    def test_name_is_unique(self) -> None:
        """The name column has a unique constraint."""
        name_col = Project.__table__.columns["name"]
        assert name_col.unique is True

    def test_category_is_not_nullable(self) -> None:
        """The category column is not nullable."""
        category_col = Project.__table__.columns["category"]
        assert category_col.nullable is False
