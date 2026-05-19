"""Tests for the Issue model."""

from craft_dashboard.models.issue import Issue


class TestIssueModel:
    """Tests for the Issue model."""

    def test_tablename(self) -> None:
        """Issue model uses 'issues' table."""
        assert Issue.__tablename__ == "issues"

    def test_required_columns(self) -> None:
        """Issue model has all required columns."""
        column_names = {col.name for col in Issue.__table__.columns}
        expected = {
            "id",
            "project_id",
            "source",
            "external_id",
            "issue_type",
            "title",
            "body",
            "state",
            "author",
            "author_is_maintainer",
            "labels",
            "created_at",
            "updated_at",
            "closed_at",
            "url",
            "metadata",
            "last_fetched_at",
        }
        assert expected.issubset(column_names)

    def test_unique_constraint(self) -> None:
        """Issue has a unique constraint on (project_id, source, external_id)."""
        constraints = Issue.__table__.constraints
        unique_constraints = [
            c
            for c in constraints
            if hasattr(c, "columns")
            and {col.name for col in c.columns}
            == {"project_id", "source", "external_id"}
        ]
        assert len(unique_constraints) == 1

    def test_project_id_foreign_key(self) -> None:
        """project_id references projects.id."""
        col = Issue.__table__.columns["project_id"]
        fk_targets = [fk.target_fullname for fk in col.foreign_keys]
        assert "projects.id" in fk_targets
