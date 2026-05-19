"""Tests for the RefreshSchedule model."""

from craft_dashboard.models.refresh_schedule import RefreshSchedule


class TestRefreshScheduleModel:
    """Tests for the RefreshSchedule model."""

    def test_tablename(self) -> None:
        """RefreshSchedule model uses 'refresh_schedule' table."""
        assert RefreshSchedule.__tablename__ == "refresh_schedule"

    def test_required_columns(self) -> None:
        """RefreshSchedule model has all required columns."""
        column_names = {col.name for col in RefreshSchedule.__table__.columns}
        expected = {
            "id",
            "project_id",
            "source",
            "next_refresh_at",
            "last_refreshed_at",
            "last_error",
            "consecutive_failures",
        }
        assert expected.issubset(column_names)

    def test_unique_constraint(self) -> None:
        """RefreshSchedule has a unique constraint on (project_id, source)."""
        constraints = RefreshSchedule.__table__.constraints
        unique_constraints = [
            c
            for c in constraints
            if hasattr(c, "columns")
            and {col.name for col in c.columns} == {"project_id", "source"}
        ]
        assert len(unique_constraints) == 1

    def test_default_consecutive_failures(self) -> None:
        """consecutive_failures defaults to 0."""
        col = RefreshSchedule.__table__.columns["consecutive_failures"]
        assert col.default.arg == 0
