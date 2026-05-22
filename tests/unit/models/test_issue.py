"""Tests for the Issue model."""

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

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

    def test_labels_annotation_uses_list_type(self) -> None:
        """Issue.labels should be annotated as Mapped[list]."""
        annotation = Issue.__annotations__["labels"]

        assert get_args(annotation) == (list,)

    def test_labels_is_list_type(self) -> None:
        """Issue.labels should be typed as list, not dict."""
        issue = Issue(
            project_id=1,
            source="github",
            external_id="1",
            issue_type="issue",
            title="test",
            state="open",
            labels=["bug", "enhancement"],
            last_fetched_at=datetime.now(tz=UTC),
        )

        assert isinstance(issue.labels, list)

    def test_boolean_columns_explicitly_set_not_nullable(self) -> None:
        """Boolean columns should explicitly set nullable=False."""
        module_path = Path(__file__).resolve().parents[3] / "craft_dashboard/models/issue.py"
        tree = ast.parse(module_path.read_text())
        issue_class = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Issue"
        )

        for field_name in ("author_is_maintainer", "author_is_bot"):
            field = next(
                node
                for node in issue_class.body
                if isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == field_name
            )
            nullable_kw = next(
                (kw.value for kw in field.value.keywords if kw.arg == "nullable"),
                None,
            )

            assert isinstance(nullable_kw, ast.Constant)
            assert nullable_kw.value is False
