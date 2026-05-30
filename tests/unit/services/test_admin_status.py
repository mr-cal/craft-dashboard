"""Tests for admin system status queries."""

from datetime import UTC, datetime, timedelta

from craft_dashboard.models.collection_run import CollectionRun
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.services.admin_service import AdminService
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"


async def _seed_status_data(session) -> None:
    now = datetime(2025, 1, 12, 15, 0, tzinfo=UTC)
    project = Project(
        name="snapcraft",
        category="application",
        github_org="canonical",
        display_order=1,
    )
    session.add(project)
    await session.flush()

    issue = Issue(
        project_id=project.id,
        source="github",
        external_id="321",
        issue_type="issue",
        title="Support core24 builds end to end",
        body="Steps to reproduce",
        state="open",
        author="sergio-cazzolato",
        author_is_maintainer=False,
        author_is_bot=False,
        labels=[],
        created_at=now - timedelta(days=3),
        updated_at=now - timedelta(hours=2),
        closed_at=None,
        url="https://github.com/canonical/snapcraft/issues/321",
        metadata_={},
        comments=[],
        last_fetched_at=now,
    )
    session.add(issue)
    await session.flush()

    session.add_all(
        [
            CollectionRun(
                source="github",
                started_at=now - timedelta(hours=4),
                finished_at=now - timedelta(hours=3, minutes=30),
                status="completed",
                projects_processed=3,
                issues_collected=10,
                errors=[],
                duration_seconds=1800.0,
            ),
            CollectionRun(
                source="launchpad",
                started_at=now - timedelta(minutes=30),
                finished_at=None,
                status="running",
                projects_processed=1,
                issues_collected=2,
                errors=[],
                duration_seconds=None,
            ),
            CollectionRun(
                source="llm",
                started_at=now - timedelta(minutes=10),
                finished_at=None,
                status="running",
                projects_processed=0,
                issues_collected=0,
                errors=[],
                duration_seconds=None,
            ),
            LLMEvaluation(
                issue_id=issue.id,
                model_name="gpt-4.1",
                summary="Regression in the core24 build pipeline.",
                suggested_action="needs_review",
                suggested_action_reason="Recent failures need maintainer attention.",
                scores={"staleness": 0.2},
                tokens_used=120,
                prompt_tokens=80,
                completion_tokens=40,
                llm_backend="openai",
                evaluated_at=now - timedelta(minutes=5),
                issue_data_hash="hash-321",
                latest=True,
            ),
        ]
    )
    await session.commit()


class TestAdminSystemStatus:
    async def test_get_system_status_reports_running_and_last_activity(
        self, test_db_session
    ) -> None:
        await _seed_status_data(test_db_session)

        status = await AdminService(test_db_session).get_system_status()

        assert status["collection_running"] is True
        assert status["evaluation_running"] is True
        assert status["last_collection"] == datetime(2025, 1, 12, 14, 30, tzinfo=UTC)
        assert status["last_evaluation"] == datetime(2025, 1, 12, 14, 55, tzinfo=UTC)

    async def test_get_system_status_handles_empty_tables(
        self, test_db_session
    ) -> None:
        status = await AdminService(test_db_session).get_system_status()

        assert status == {
            "collection_running": False,
            "evaluation_running": False,
            "last_collection": None,
            "last_evaluation": None,
        }
