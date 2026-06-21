"""Tests for admin collection health queries."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from craft_dashboard.services.admin_service import AdminService


class _FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self._values


class TestAdminCollectionHealth:
    async def test_get_recent_collection_runs_returns_most_recent_first(self) -> None:
        now = datetime(2025, 1, 10, 12, 0, tzinfo=UTC)
        session = MagicMock()
        session.execute = AsyncMock(
            return_value=_FakeScalarResult(
                [
                    SimpleNamespace(
                        id=1,
                        source="launchpad",
                        started_at=now - timedelta(hours=1),
                        finished_at=now - timedelta(minutes=59),
                        status="failed",
                        projects_processed=2,
                        issues_collected=5,
                        errors=[{"project": "snapcraft", "error": "timeout"}],
                        duration_seconds=60.0,
                    ),
                    SimpleNamespace(
                        id=2,
                        source="github",
                        started_at=now - timedelta(hours=2),
                        finished_at=now - timedelta(hours=2) + timedelta(minutes=3),
                        status="completed",
                        projects_processed=3,
                        issues_collected=12,
                        errors=[],
                        duration_seconds=180.0,
                    ),
                ]
            )
        )

        runs = await AdminService(session).get_recent_collection_runs(limit=2)

        assert [run["source"] for run in runs] == ["launchpad", "github"]
        assert runs[0]["status"] == "failed"
        assert runs[0]["errors"] == [{"project": "snapcraft", "error": "timeout"}]
        assert runs[1]["projects_processed"] == 3
        assert runs[1]["issues_collected"] == 12
        assert runs[0]["id"] == 1
        assert runs[1]["id"] == 2
