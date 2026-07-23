"""Tests for the admin service."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from craft_dashboard.models.collection_run import CollectionRun
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.issue_activity import IssueActivity
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


class TestGetRecentIssueActivity:
    async def test_returns_most_recent_issue_activity_first(
        self, test_db_session
    ) -> None:
        project = Project(
            name="snapcraft",
            category="application",
            github_org="canonical",
            display_order=1,
        )
        test_db_session.add(project)
        await test_db_session.flush()

        test_db_session.add_all(
            [
                IssueActivity(
                    project_id=project.id,
                    issue_number=11,
                    change_type="opened",
                    title="Oldest change",
                    occurred_at=datetime(2025, 1, 10, 9, 0, tzinfo=UTC),
                ),
                IssueActivity(
                    project_id=project.id,
                    issue_number=12,
                    change_type="updated",
                    title="Newest change",
                    occurred_at=datetime(2025, 1, 10, 11, 0, tzinfo=UTC),
                ),
                IssueActivity(
                    project_id=project.id,
                    issue_number=13,
                    change_type="closed",
                    title="Middle change",
                    occurred_at=datetime(2025, 1, 10, 10, 0, tzinfo=UTC),
                ),
            ]
        )
        await test_db_session.commit()

        activities, total = await AdminService(
            test_db_session
        ).get_recent_issue_activity(limit=2)

        assert [activity["title"] for activity in activities] == [
            "Newest change",
            "Middle change",
        ]
        assert activities[0]["project"] == "snapcraft"
        assert activities[0]["number"] == "12"
        assert activities[0]["change_type"] == "updated"
        assert activities[0]["issue_type"] == "issue"
        assert total == 3

    async def test_respects_offset(self, test_db_session) -> None:
        """Offset skips the newest rows, for paging further back in time."""
        project = Project(
            name="snapcraft",
            category="application",
            github_org="canonical",
            display_order=1,
        )
        test_db_session.add(project)
        await test_db_session.flush()

        test_db_session.add_all(
            [
                IssueActivity(
                    project_id=project.id,
                    issue_number=11,
                    change_type="opened",
                    title="Oldest change",
                    occurred_at=datetime(2025, 1, 10, 9, 0, tzinfo=UTC),
                ),
                IssueActivity(
                    project_id=project.id,
                    issue_number=12,
                    change_type="updated",
                    title="Newest change",
                    occurred_at=datetime(2025, 1, 10, 11, 0, tzinfo=UTC),
                ),
                IssueActivity(
                    project_id=project.id,
                    issue_number=13,
                    change_type="closed",
                    title="Middle change",
                    occurred_at=datetime(2025, 1, 10, 10, 0, tzinfo=UTC),
                ),
            ]
        )
        await test_db_session.commit()

        activities, total = await AdminService(
            test_db_session
        ).get_recent_issue_activity(limit=2, offset=2)

        assert [activity["title"] for activity in activities] == ["Oldest change"]
        assert total == 3

    async def test_joins_issue_for_live_url(self, test_db_session) -> None:
        """The issue's live GitHub URL is joined in when the issue still exists."""
        project = Project(
            name="snapcraft",
            category="application",
            github_org="canonical",
            display_order=1,
        )
        test_db_session.add(project)
        await test_db_session.flush()

        test_db_session.add(
            Issue(
                project_id=project.id,
                source="github",
                external_id="12",
                issue_type="issue",
                title="Live issue title",
                state="open",
                url="https://github.com/canonical/snapcraft/issues/12",
                last_fetched_at=datetime(2025, 1, 10, 11, 0, tzinfo=UTC),
            )
        )
        test_db_session.add(
            IssueActivity(
                project_id=project.id,
                issue_number=12,
                change_type="updated",
                title="Recorded title",
                occurred_at=datetime(2025, 1, 10, 11, 0, tzinfo=UTC),
            )
        )
        await test_db_session.commit()

        activities, _total = await AdminService(
            test_db_session
        ).get_recent_issue_activity()

        assert (
            activities[0]["url"] == "https://github.com/canonical/snapcraft/issues/12"
        )
        assert activities[0]["issue_type"] == "issue"

    async def test_excludes_filtered_issues(self, test_db_session) -> None:
        """Dependency-dashboard issues listed in filtered_issues are excluded."""
        project = Project(
            name="snapcraft",
            category="application",
            github_org="canonical",
            display_order=1,
        )
        test_db_session.add(project)
        await test_db_session.flush()

        test_db_session.add_all(
            [
                IssueActivity(
                    project_id=project.id,
                    issue_number=4472,
                    change_type="updated",
                    title="Dependency Dashboard",
                    occurred_at=datetime(2025, 1, 10, 12, 0, tzinfo=UTC),
                ),
                IssueActivity(
                    project_id=project.id,
                    issue_number=12,
                    change_type="updated",
                    title="A real issue",
                    occurred_at=datetime(2025, 1, 10, 11, 0, tzinfo=UTC),
                ),
            ]
        )
        await test_db_session.commit()

        activities, _total = await AdminService(
            test_db_session
        ).get_recent_issue_activity(filtered_issues={"snapcraft": ["4472"]})

        assert [activity["number"] for activity in activities] == ["12"]


class TestGetIssuesForRun:
    async def test_change_type_joined_from_matching_activity(
        self, test_db_session
    ) -> None:
        """An issue changed by this run shows its recorded change_type."""
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
            status="completed",
            started_at=datetime(2025, 1, 10, 10, 0, tzinfo=UTC),
        )
        test_db_session.add(run)
        await test_db_session.flush()

        test_db_session.add(
            Issue(
                project_id=project.id,
                source="github",
                external_id="12",
                issue_type="issue",
                title="Newly opened issue",
                state="open",
                url="https://github.com/canonical/snapcraft/issues/12",
                last_fetched_at=datetime(2025, 1, 10, 11, 0, tzinfo=UTC),
                collection_run_id=run.id,
            )
        )
        test_db_session.add(
            IssueActivity(
                project_id=project.id,
                issue_number=12,
                change_type="created",
                title="Newly opened issue",
                occurred_at=datetime(2025, 1, 10, 11, 0, tzinfo=UTC),
                collection_run_id=run.id,
            )
        )
        await test_db_session.commit()

        issues, total = await AdminService(test_db_session).get_issues_for_run(run.id)

        assert total == 1
        assert issues[0]["change_type"] == "created"
        assert issues[0]["project"] == "snapcraft"
        assert issues[0]["number"] == "12"
        assert issues[0]["issue_type"] == "issue"
        assert issues[0]["url"] == "https://github.com/canonical/snapcraft/issues/12"

    async def test_unchanged_issue_falls_back_to_unchanged(
        self, test_db_session
    ) -> None:
        """An issue collected but not changed this run shows change_type 'unchanged'."""
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
            status="completed",
            started_at=datetime(2025, 1, 10, 10, 0, tzinfo=UTC),
        )
        test_db_session.add(run)
        await test_db_session.flush()

        test_db_session.add(
            Issue(
                project_id=project.id,
                source="github",
                external_id="13",
                issue_type="issue",
                title="Untouched issue",
                state="open",
                url="https://github.com/canonical/snapcraft/issues/13",
                last_fetched_at=datetime(2025, 1, 10, 11, 0, tzinfo=UTC),
                collection_run_id=run.id,
            )
        )
        await test_db_session.commit()

        issues, total = await AdminService(test_db_session).get_issues_for_run(run.id)

        assert total == 1
        assert issues[0]["change_type"] == "unchanged"
        occurred_at = issues[0]["occurred_at"]
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        assert occurred_at == datetime(2025, 1, 10, 11, 0, tzinfo=UTC)

    async def test_excludes_filtered_issues(self, test_db_session) -> None:
        """Dependency-dashboard issues listed in filtered_issues are excluded."""
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
            status="completed",
            started_at=datetime(2025, 1, 10, 10, 0, tzinfo=UTC),
        )
        test_db_session.add(run)
        await test_db_session.flush()

        test_db_session.add_all(
            [
                Issue(
                    project_id=project.id,
                    source="github",
                    external_id="4472",
                    issue_type="issue",
                    title="Dependency Dashboard",
                    state="open",
                    url="https://github.com/canonical/snapcraft/issues/4472",
                    last_fetched_at=datetime(2025, 1, 10, 11, 0, tzinfo=UTC),
                    collection_run_id=run.id,
                ),
                Issue(
                    project_id=project.id,
                    source="github",
                    external_id="12",
                    issue_type="issue",
                    title="A real issue",
                    state="open",
                    url="https://github.com/canonical/snapcraft/issues/12",
                    last_fetched_at=datetime(2025, 1, 10, 11, 0, tzinfo=UTC),
                    collection_run_id=run.id,
                ),
            ]
        )
        await test_db_session.commit()

        issues, total = await AdminService(test_db_session).get_issues_for_run(
            run.id, filtered_issues={"snapcraft": ["4472"]}
        )

        assert total == 1
        assert [issue["number"] for issue in issues] == ["12"]


class TestGetApiBudget:
    async def test_checks_rate_limit_off_event_loop(self, test_db_session) -> None:
        api_token = "unit-test-token"
        expected_budget = {
            "core_remaining": 4999,
            "core_limit": 5000,
            "core_reset": datetime(2025, 1, 10, 12, 30, tzinfo=UTC),
            "graphql_remaining": 4998,
            "graphql_limit": 5000,
            "graphql_reset": datetime(2025, 1, 10, 12, 45, tzinfo=UTC),
        }

        with (
            patch(
                "craft_dashboard.services.admin_service.Settings",
                return_value=SimpleNamespace(github_token=api_token),
            ),
            patch(
                "craft_dashboard.services.admin_service.GitHubCollector"
            ) as collector,
            patch(
                "craft_dashboard.services.admin_service.asyncio.to_thread"
            ) as to_thread,
        ):
            to_thread.return_value = expected_budget

            budget = await AdminService(test_db_session).get_api_budget()

        assert collector.call_args.kwargs == {"token": api_token}
        to_thread.assert_awaited_once_with(collector.return_value.check_rate_limit)
        assert budget == expected_budget


class TestGetNextExpectedFetch:
    async def test_returns_ten_minutes_after_latest_completed_github_run(
        self, test_db_session
    ) -> None:
        test_db_session.add_all(
            [
                CollectionRun(
                    source="github",
                    started_at=datetime(2025, 1, 10, 9, 40, tzinfo=UTC),
                    finished_at=datetime(2025, 1, 10, 9, 42, tzinfo=UTC),
                    status="completed",
                    projects_processed=1,
                    issues_collected=10,
                    errors=[],
                    duration_seconds=120.0,
                ),
                CollectionRun(
                    source="github",
                    started_at=datetime(2025, 1, 10, 10, 0, tzinfo=UTC),
                    finished_at=None,
                    status="running",
                    projects_processed=1,
                    issues_collected=3,
                    errors=[],
                    duration_seconds=None,
                ),
                CollectionRun(
                    source="github",
                    started_at=datetime(2025, 1, 10, 9, 55, tzinfo=UTC),
                    finished_at=datetime(2025, 1, 10, 9, 57, tzinfo=UTC),
                    status="failed",
                    projects_processed=1,
                    issues_collected=2,
                    errors=[{"error": "boom"}],
                    duration_seconds=120.0,
                ),
                CollectionRun(
                    source="launchpad",
                    started_at=datetime(2025, 1, 10, 10, 5, tzinfo=UTC),
                    finished_at=datetime(2025, 1, 10, 10, 6, tzinfo=UTC),
                    status="completed",
                    projects_processed=1,
                    issues_collected=4,
                    errors=[],
                    duration_seconds=60.0,
                ),
            ]
        )
        await test_db_session.commit()

        next_fetch = await AdminService(test_db_session).get_next_expected_fetch()

        assert next_fetch == datetime(2025, 1, 10, 9, 50, tzinfo=UTC)

    async def test_returns_none_when_no_completed_github_runs_exist(
        self, test_db_session
    ) -> None:
        test_db_session.add_all(
            [
                CollectionRun(
                    source="github",
                    started_at=datetime(2025, 1, 10, 10, 0, tzinfo=UTC),
                    finished_at=None,
                    status="running",
                    projects_processed=1,
                    issues_collected=0,
                    errors=[],
                    duration_seconds=None,
                ),
                CollectionRun(
                    source="launchpad",
                    started_at=datetime(2025, 1, 10, 10, 5, tzinfo=UTC),
                    finished_at=datetime(2025, 1, 10, 10, 6, tzinfo=UTC),
                    status="completed",
                    projects_processed=1,
                    issues_collected=4,
                    errors=[],
                    duration_seconds=60.0,
                ),
            ]
        )
        await test_db_session.commit()

        next_fetch = await AdminService(test_db_session).get_next_expected_fetch()

        assert next_fetch is None


class TestLLMServiceStatus:
    """Tests for AdminService.get_llm_service_status."""

    async def test_status_is_unknown_when_never_polled(self, test_db_session) -> None:
        """No recorded activity yet (e.g. right after a fresh deploy)."""
        with patch(
            "craft_dashboard.services.admin_service.get_eval_activity",
            return_value=(None, None),
        ):
            status = await AdminService(test_db_session).get_llm_service_status()

        assert status == {
            "status": "unknown",
            "last_poll_at": None,
            "last_result_at": None,
        }

    async def test_status_is_running_when_recently_polled(
        self, test_db_session
    ) -> None:
        """A poll within the stale window counts as running."""
        now = datetime(2025, 1, 10, 12, 0, tzinfo=UTC)
        last_poll = now - timedelta(seconds=10)
        last_result = now - timedelta(seconds=40)
        with (
            patch(
                "craft_dashboard.services.admin_service.get_eval_activity",
                return_value=(last_poll, last_result),
            ),
            patch(
                "craft_dashboard.services.admin_service.datetime", FrozenStatsDateTime
            ),
        ):
            FrozenStatsDateTime.frozen_now = now
            status = await AdminService(test_db_session).get_llm_service_status()

        assert status == {
            "status": "running",
            "last_poll_at": last_poll,
            "last_result_at": last_result,
        }

    async def test_status_is_stalled_when_poll_is_too_old(
        self, test_db_session
    ) -> None:
        """A poll older than the stale window counts as stalled."""
        now = datetime(2025, 1, 10, 12, 0, tzinfo=UTC)
        last_poll = now - timedelta(minutes=10)
        with (
            patch(
                "craft_dashboard.services.admin_service.get_eval_activity",
                return_value=(last_poll, None),
            ),
            patch(
                "craft_dashboard.services.admin_service.datetime", FrozenStatsDateTime
            ),
        ):
            FrozenStatsDateTime.frozen_now = now
            status = await AdminService(test_db_session).get_llm_service_status()

        assert status["status"] == "stalled"


class TestRecentEvaluations:
    """Tests for AdminService.get_recent_evaluations."""

    async def test_returns_evaluations_newest_first(self, test_db_session) -> None:
        await _seed_admin_data(test_db_session)

        recent, total = await AdminService(test_db_session).get_recent_evaluations(
            limit=20
        )

        assert [entry["suggested_action"] for entry in recent] == [
            "keep_open",
            "needs_review",
        ]
        assert recent[0]["project"] == "snapcraft"
        assert recent[0]["model_name"] == "gpt-4.1"
        assert "tokens_used" not in recent[0]
        assert total == 2

    async def test_respects_limit(self, test_db_session) -> None:
        await _seed_admin_data(test_db_session)

        recent, total = await AdminService(test_db_session).get_recent_evaluations(
            limit=1
        )

        assert len(recent) == 1
        assert recent[0]["suggested_action"] == "keep_open"
        assert total == 2

    async def test_respects_offset(self, test_db_session) -> None:
        await _seed_admin_data(test_db_session)

        recent, total = await AdminService(test_db_session).get_recent_evaluations(
            limit=1, offset=1
        )

        assert len(recent) == 1
        assert recent[0]["suggested_action"] == "needs_review"
        assert total == 2


class TestDailyEvaluationStats:
    """Tests for AdminService.get_daily_evaluation_stats."""

    async def test_counts_evaluations_per_day_within_window(
        self, test_db_session
    ) -> None:
        await _seed_admin_data(test_db_session)

        with patch(
            "craft_dashboard.services.admin_service.datetime", FrozenStatsDateTime
        ):
            FrozenStatsDateTime.frozen_now = datetime(2025, 1, 10, 12, 0, tzinfo=UTC)
            stats = await AdminService(test_db_session).get_daily_evaluation_stats(
                days=7
            )

        # Only the evaluation from 2 days ago falls within the 7-day window;
        # the one from 10 days ago is excluded.
        assert stats == [{"date": "2025-01-08", "count": 1}]

    async def test_excludes_evaluations_outside_window(self, test_db_session) -> None:
        await _seed_admin_data(test_db_session)

        with patch(
            "craft_dashboard.services.admin_service.datetime", FrozenStatsDateTime
        ):
            FrozenStatsDateTime.frozen_now = datetime(2025, 1, 10, 12, 0, tzinfo=UTC)
            stats = await AdminService(test_db_session).get_daily_evaluation_stats(
                days=14
            )

        assert len(stats) == 2
