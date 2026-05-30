"""Tests for the Issue model."""

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args, get_origin

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
            title="snapcraft pack fails with LXD backend on Ubuntu 24.04",
            state="open",
            labels=["bug", "priority-high"],
            last_fetched_at=datetime.now(tz=UTC),
        )

        assert isinstance(issue.labels, list)

    def test_boolean_columns_explicitly_set_not_nullable(self) -> None:
        """Boolean columns should explicitly set nullable=False."""
        module_path = (
            Path(__file__).resolve().parents[3] / "craft_dashboard/models/issue.py"
        )
        tree = ast.parse(module_path.read_text())
        issue_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Issue"
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

    def test_json_annotations_are_specific(self) -> None:
        """Issue JSONB fields should use specific typed annotations."""
        metadata_type = get_args(Issue.__annotations__["metadata_"])[0]
        comments_type = get_args(Issue.__annotations__["comments"])[0]

        assert get_origin(metadata_type) is dict
        assert get_args(metadata_type) == (str, Any)
        assert get_origin(comments_type) is list
        assert get_args(get_args(comments_type)[0]) == (str, Any)

    def test_issue_created_at_has_server_default(self) -> None:
        """Issue.created_at should fall back to a DB-side timestamp."""
        created_at = Issue.__table__.columns["created_at"]

        assert created_at.server_default is not None
