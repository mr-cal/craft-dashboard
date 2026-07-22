"""Tests for admin collection health queries."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from craft_dashboard.models.collection_run import CollectionRun
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.project import Project
from craft_dashboard.services.admin_service import AdminService
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"


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

    async def test_issues_collected_subtracts_filtered_issues(
        self, test_db_session
    ) -> None:
        """The displayed count should exclude issues in the [issues.filter] config.

        Otherwise the admin page can show e.g. "1 issue collected" for a run
        that only collected a filtered-out issue, and clicking through shows
        an empty list.
        """
        now = datetime(2025, 1, 10, 12, 0, tzinfo=UTC)
        project = Project(
            name="snapcraft",
            category="application",
            github_org="canonical",
            display_order=1,
        )
        test_db_session.add(project)
        await test_db_session.flush()

        run = CollectionRun(
            source="github",
            started_at=now - timedelta(hours=1),
            finished_at=now - timedelta(minutes=59),
            status="completed",
            projects_processed=1,
            issues_collected=2,
            errors=[],
            duration_seconds=60.0,
        )
        test_db_session.add(run)
        await test_db_session.flush()

        test_db_session.add_all(
            [
                Issue(
                    project_id=project.id,
                    source="github",
                    external_id="1",
                    issue_type="issue",
                    title="Dependency Dashboard",
                    state="open",
                    labels=[],
                    last_fetched_at=now,
                    collection_run_id=run.id,
                ),
                Issue(
                    project_id=project.id,
                    source="github",
                    external_id="2",
                    issue_type="issue",
                    title="Real bug report",
                    state="open",
                    labels=[],
                    last_fetched_at=now,
                    collection_run_id=run.id,
                ),
            ]
        )
        await test_db_session.commit()

        runs = await AdminService(test_db_session).get_recent_collection_runs(
            filtered_issues={"snapcraft": ["1"]}
        )

        assert runs[0]["issues_collected"] == 1

    async def test_issues_collected_unchanged_without_filtered_issues(
        self, test_db_session
    ) -> None:
        now = datetime(2025, 1, 10, 12, 0, tzinfo=UTC)
        test_db_session.add(
            CollectionRun(
                source="github",
                started_at=now,
                finished_at=now,
                status="completed",
                projects_processed=1,
                issues_collected=5,
                errors=[],
                duration_seconds=10.0,
            )
        )
        await test_db_session.commit()

        runs = await AdminService(test_db_session).get_recent_collection_runs()

        assert runs[0]["issues_collected"] == 5
