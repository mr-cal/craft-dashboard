"""Tests for E2E seed data generation."""

from tests.end_to_end.seed_data import PROJECTS, _make_releases, generate_seed_sql


class TestSeedDataSQL:
    def test_seed_sql_uses_correct_table_names(self):
        """Table names in seed SQL must match SQLAlchemy model __tablename__."""
        sql = generate_seed_sql()
        assert "refresh_schedules" not in sql, (
            "seed SQL references 'refresh_schedules' but model uses 'refresh_schedule'"
        )
        assert "DELETE FROM refresh_schedule;" in sql

    def test_seed_releases_have_unique_branches(self):
        """Each project's releases must have unique (project_id, branch) pairs."""
        for pid, proj in enumerate(PROJECTS, start=1):
            releases = _make_releases(pid, proj["name"])
            branches = [(r["project_id"], r["branch"]) for r in releases]
            assert len(branches) == len(set(branches)), (
                f"{proj['name']}: duplicate (project_id, branch) in releases: {branches}"
            )
