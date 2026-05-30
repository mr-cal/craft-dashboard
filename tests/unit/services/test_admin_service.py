"""Tests for the admin service."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.models.refresh_schedule import RefreshSchedule
from craft_dashboard.services.admin_service import AdminService
from sqlalchemy import select
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"


class FrozenStatsDateTime(datetime):
    """Frozen datetime for deterministic token stats."""

    frozen_now = datetime(2025, 1, 10, 12, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        """Return a fixed current time."""
        if tz is None:
            return cls.frozen_now.replace(tzinfo=None)
        return cls.frozen_now.astimezone(tz)


class FrozenScheduleDateTime(datetime):
    """Frozen datetime for deterministic schedule updates."""

    frozen_now = datetime(2025, 1, 6, 9, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        """Return a fixed current time."""
        if tz is None:
            return cls.frozen_now.replace(tzinfo=None)
        return cls.frozen_now.astimezone(tz)


async def _seed_admin_data(session) -> None:
    aggregate = Project(
        name="all-craft", category="aggregate", github_org="canonical", display_order=0
    )
    charmcraft = Project(
        name="charmcraft", category="library", github_org="canonical", display_order=1
    )
    snapcraft = Project(
        name="snapcraft",
        category="application",
        github_org="canonical",
        display_order=2,
    )
    session.add_all([aggregate, charmcraft, snapcraft])
    await session.flush()

    now = datetime(2025, 1, 10, 12, 0, tzinfo=UTC)
    session.add_all(
        [
            Issue(
                project_id=snapcraft.id,
                source="github",
                external_id="1",
                issue_type="issue",
                title="Recent issue",
                body="Recent issue body",
                state="open",
                author="dev",
                author_is_maintainer=False,
                author_is_bot=False,
                labels=[],
                created_at=now - timedelta(days=4),
                updated_at=now - timedelta(days=1),
                closed_at=None,
                url="https://example.com/snapcraft/issues/1",
                metadata_={},
                comments=[],
                last_fetched_at=now,
            ),
            Issue(
                project_id=charmcraft.id,
                source="github",
                external_id="2",
                issue_type="issue",
                title="Older issue",
                body="Older issue body",
                state="open",
                author="dev",
                author_is_maintainer=False,
                author_is_bot=False,
                labels=[],
                created_at=now - timedelta(days=12),
                updated_at=now - timedelta(days=8),
                closed_at=None,
                url="https://example.com/charmcraft/issues/2",
                metadata_={},
                comments=[],
                last_fetched_at=now,
            ),
        ]
    )
    await session.flush()

    issues = (await session.execute(select(Issue).order_by(Issue.id))).scalars().all()
    recent_issue, older_issue = issues

    session.add_all(
        [
            LLMEvaluation(
                issue_id=recent_issue.id,
                model_name="gpt-4.1",
                summary="Recent evaluation",
                suggested_action="keep_open",
                suggested_action_reason="Still active",
                scores={},
                tokens_used=120,
                prompt_tokens=70,
                completion_tokens=50,
                llm_backend="test",
                evaluated_at=now - timedelta(days=2),
                issue_data_hash="recent",
                latest=True,
            ),
            LLMEvaluation(
                issue_id=older_issue.id,
                model_name="gpt-4.1",
                summary="Older evaluation",
                suggested_action="needs_review",
                suggested_action_reason="Needs follow-up",
                scores={},
                tokens_used=300,
                prompt_tokens=200,
                completion_tokens=100,
                llm_backend="test",
                evaluated_at=now - timedelta(days=10),
                issue_data_hash="older",
                latest=True,
            ),
        ]
    )

    session.add_all(
        [
            RefreshSchedule(
                project_id=snapcraft.id,
                source="github",
                next_refresh_at=datetime(2025, 1, 6, 10, 0, tzinfo=UTC),
            ),
            RefreshSchedule(
                project_id=snapcraft.id,
                source="launchpad",
                next_refresh_at=datetime(2025, 1, 8, 10, 0, tzinfo=UTC),
            ),
            RefreshSchedule(
                project_id=charmcraft.id,
                source="github",
                next_refresh_at=datetime(2025, 1, 10, 10, 0, tzinfo=UTC),
            ),
            RefreshSchedule(
                project_id=aggregate.id,
                source="github",
                next_refresh_at=datetime(2025, 1, 12, 10, 0, tzinfo=UTC),
            ),
        ]
    )
    await session.commit()


class TestAdminService:
    async def test_get_lifetime_token_stats_returns_aggregate_totals(
        self, test_db_session
    ) -> None:
        """Lifetime token stats include all evaluations."""
        await _seed_admin_data(test_db_session)

        stats = await AdminService(test_db_session).get_lifetime_token_stats()

        assert stats == {
            "evaluations": 2,
            "tokens": 420,
            "prompt_tokens": 270,
            "completion_tokens": 150,
        }

    async def test_get_token_stats_filters_to_recent_days(
        self, test_db_session
    ) -> None:
        """Token stats can be filtered to a trailing day window."""
        await _seed_admin_data(test_db_session)

        with patch(
            "craft_dashboard.services.admin_service.datetime", FrozenStatsDateTime
        ):
            stats = await AdminService(test_db_session).get_token_stats(days=7)

        assert stats == {
            "evaluations": 1,
            "tokens": 120,
            "prompt_tokens": 70,
            "completion_tokens": 50,
        }

    async def test_get_seven_day_token_stats_uses_seven_day_window(
        self, test_db_session
    ) -> None:
        """Seven day token stats delegate to the shared filtered query."""
        await _seed_admin_data(test_db_session)

        with patch(
            "craft_dashboard.services.admin_service.datetime", FrozenStatsDateTime
        ):
            stats = await AdminService(test_db_session).get_seven_day_token_stats()

        assert stats == {
            "evaluations": 1,
            "tokens": 120,
            "prompt_tokens": 70,
            "completion_tokens": 50,
        }

    async def test_get_schedule_groups_days_by_project(self, test_db_session) -> None:
        """Schedules are grouped by project with unique weekday values."""
        await _seed_admin_data(test_db_session)

        schedule = await AdminService(test_db_session).get_schedule()

        assert schedule == [
            {"project": "charmcraft", "days": [4]},
            {"project": "snapcraft", "days": [0, 2]},
        ]

    async def test_get_project_names_excludes_aggregate_projects(
        self, test_db_session
    ) -> None:
        """Project names exclude aggregate projects and keep display order."""
        await _seed_admin_data(test_db_session)

        project_names = await AdminService(test_db_session).get_project_names()

        assert project_names == ["charmcraft", "snapcraft"]

    async def test_update_schedule_sets_next_refresh_dates_for_project(
        self, test_db_session
    ) -> None:
        """Updating a project schedule rewrites its source refresh dates."""
        await _seed_admin_data(test_db_session)

        with patch(
            "craft_dashboard.services.admin_service.datetime", FrozenScheduleDateTime
        ):
            await AdminService(test_db_session).update_schedule("snapcraft", [1, 3])

        result = await test_db_session.execute(
            select(RefreshSchedule)
            .join(Project, Project.id == RefreshSchedule.project_id)
            .where(Project.name == "snapcraft")
            .order_by(RefreshSchedule.source)
        )
        schedules = result.scalars().all()

        assert [schedule.next_refresh_at for schedule in schedules] == [
            datetime(2025, 1, 7, 9, 0),
            datetime(2025, 1, 9, 9, 0),
        ]
