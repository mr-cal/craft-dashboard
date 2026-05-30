"""Tests for scripts.llm.queries."""

from datetime import UTC, datetime

from scripts.llm.queries import _build_issue_query
from sqlalchemy.dialects import postgresql as pg_dialect


def _compile(query: object) -> str:
    return str(query.compile(dialect=pg_dialect.dialect()))


class TestBuildIssueQuery:
    def test_applies_project_and_open_filters(self) -> None:
        query = _build_issue_query(project_filter="snapcraft", open_only=True)

        compiled = _compile(query)

        assert "JOIN projects ON issues.project_id = projects.id" in compiled
        assert "LEFT OUTER JOIN llm_evaluations" in compiled
        assert "issues.state = %(state_1)s" in compiled
        assert "projects.name = %(name_1)s" in compiled

    def test_applies_issue_range_filters_with_integer_cast(self) -> None:
        query = _build_issue_query(
            issue_filters=[("charmcraft", 100, 200), ("snapcraft", 7, 7)]
        )

        compiled = _compile(query)

        assert "CAST(issues.external_id AS INTEGER) >= %(param_1)s" in compiled
        assert "CAST(issues.external_id AS INTEGER) <= %(param_2)s" in compiled
        assert "projects.name = %(name_1)s" in compiled
        assert "projects.name = %(name_2)s" in compiled
        assert " OR " in compiled

    def test_applies_incomplete_and_stale_filters(self) -> None:
        query = _build_issue_query(incomplete=True, stale_days=30)

        compiled = _compile(query)
        params = query.compile(dialect=pg_dialect.dialect()).params

        assert "llm_evaluations.issue_id IS NULL" in compiled
        assert "llm_evaluations.summary IS NULL" in compiled
        assert "issues.state = %(state_1)s" in compiled
        assert "llm_evaluations.suggested_action = %(suggested_action_1)s" in compiled
        assert "llm_evaluations.scores IS NULL" in compiled
        assert "llm_evaluations.evaluated_at < %(evaluated_at_1)s" in compiled
        assert isinstance(params["evaluated_at_1"], datetime)
        assert params["evaluated_at_1"].tzinfo is UTC
