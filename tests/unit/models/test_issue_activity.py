"""Tests for the IssueActivity model."""

from datetime import UTC, datetime

import pytest
from craft_dashboard.models.issue_activity import IssueActivity
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

from tests.factories import make_project

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"


class TestIssueActivityModel:
    """Tests for the IssueActivity model."""

    def test_tablename(self) -> None:
        """IssueActivity model uses 'issue_activities' table."""
        assert IssueActivity.__tablename__ == "issue_activities"

    def test_required_columns(self) -> None:
        """IssueActivity model has all required columns."""
        column_names = {col.name for col in IssueActivity.__table__.columns}
        expected = {
            "id",
            "project_id",
            "issue_number",
            "change_type",
            "title",
            "occurred_at",
            "collection_run_id",
        }
        assert expected.issubset(column_names)

    def test_project_id_foreign_key(self) -> None:
        """project_id references projects.id with cascade delete."""
        col = IssueActivity.__table__.columns["project_id"]
        foreign_key = next(iter(col.foreign_keys))

        assert foreign_key.target_fullname == "projects.id"
        assert foreign_key.ondelete == "CASCADE"

    def test_collection_run_id_foreign_key(self) -> None:
        """collection_run_id references collection_runs.id, nullable, SET NULL."""
        col = IssueActivity.__table__.columns["collection_run_id"]
        foreign_key = next(iter(col.foreign_keys))

        assert foreign_key.target_fullname == "collection_runs.id"
        assert foreign_key.ondelete == "SET NULL"
        assert col.nullable is True

    @pytest.mark.asyncio
    async def test_create_and_read_issue_activity(self, test_db_session) -> None:
        """IssueActivity can be stored and reloaded."""
        project = make_project()
        test_db_session.add(project)
        await test_db_session.flush()

        activity = IssueActivity(
            project_id=project.id,
            issue_number=42,
            change_type="updated",
            title="Label added: needs-review",
            occurred_at=datetime.now(UTC),
        )
        test_db_session.add(activity)
        await test_db_session.commit()
        await test_db_session.refresh(activity)

        assert activity.id is not None
        assert activity.change_type == "updated"
        assert activity.project_id == project.id
        assert activity.issue_number == 42
