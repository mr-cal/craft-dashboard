"""Unit tests for DashboardService."""

from datetime import UTC, datetime, timedelta

import pytest
from craft_dashboard.config import DashboardConfig
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.models.release import Release
from craft_dashboard.services.dashboard_service import (
    DashboardService,
    compute_release_badge_color,
    compute_triage_badge_color,
)
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"

_idx = next(
    (
        i
        for i in LLMEvaluation.__table__.indexes
        if i.name == "ix_llm_evaluations_latest_issue"
    ),
    None,
)
if _idx is not None:
    _idx.dialect_options.pop("postgresql", None)


class TestColorRules:
    """Test the threshold color helper functions."""

    def test_triage_badge_color_rules(self) -> None:
        # > 20% is red
        assert compute_triage_badge_color(21, 100) == "red"
        # > 10% and <= 20% is yellow
        assert compute_triage_badge_color(15, 100) == "yellow"
        assert compute_triage_badge_color(20, 100) == "yellow"
        # <= 10% is green
        assert compute_triage_badge_color(10, 100) == "green"
        assert compute_triage_badge_color(1, 100) == "green"
        # 0 or negative is neutral
        assert compute_triage_badge_color(0, 100) == "neutral"
        assert compute_triage_badge_color(5, 0) == "neutral"

    def test_release_badge_color_rules(self) -> None:
        # > 60d is red
        assert compute_release_badge_color(61) == "red"
        assert compute_release_badge_color(120) == "red"
        # > 30d and <= 60d is yellow
        assert compute_release_badge_color(31) == "yellow"
        assert compute_release_badge_color(60) == "yellow"
        # <= 30d is green
        assert compute_release_badge_color(30) == "green"
        assert compute_release_badge_color(0) == "green"
        # None is neutral
        assert compute_release_badge_color(None) == "neutral"


class TestDashboardService:
    """Test queries and aggregations in DashboardService."""

    @pytest.mark.asyncio
    async def test_homepage_metrics_and_cadence(
        self, test_db_session: AsyncSession
    ) -> None:
        now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)

        # Seed projects
        app_project = Project(
            name="snapcraft",
            category="application",
            github_org="canonical",
            display_order=1,
        )
        unreleased_app = Project(
            name="debcraft",
            category="application",
            github_org="canonical",
            display_order=2,
        )
        lib_project = Project(
            name="craft-parts",
            category="library",
            github_org="canonical",
            display_order=3,
        )
        test_db_session.add_all([app_project, unreleased_app, lib_project])
        await test_db_session.flush()

        # Seed releases
        rel1 = Release(
            project_id=app_project.id,
            version="8.4.0",
            released_at=now - timedelta(days=40),  # Yellow
            metadata_={"commits_since_tag": 12},
        )
        rel2 = Release(
            project_id=lib_project.id,
            version="1.2.0",
            released_at=now - timedelta(days=15),  # Green
            metadata_={"commits_since_tag": 2},
        )
        test_db_session.add_all([rel1, rel2])

        # Seed PRs:
        # 1. Contributor PR (created 20 days ago)
        pr_contrib = Issue(
            project_id=app_project.id,
            source="github",
            external_id="101",
            issue_type="pull_request",
            title="Community feature PR",
            state="open",
            author="external-user",
            author_is_maintainer=False,
            author_is_bot=False,
            created_at=now - timedelta(days=20),
            last_fetched_at=now,
        )
        # 2. Maintainer PR (created 10 days ago)
        pr_maint = Issue(
            project_id=app_project.id,
            source="github",
            external_id="102",
            issue_type="pull_request",
            title="Maintainer bugfix PR",
            state="open",
            author="core-maintainer",
            author_is_maintainer=True,
            author_is_bot=False,
            created_at=now - timedelta(days=10),
            last_fetched_at=now,
        )
        # 3. Closed PR (closed 5 days ago, inside 30d)
        pr_closed_30d = Issue(
            project_id=lib_project.id,
            source="github",
            external_id="103",
            issue_type="pull_request",
            title="Merged PR",
            state="closed",
            author="core-maintainer",
            created_at=now - timedelta(days=15),
            closed_at=now - timedelta(days=5),
            last_fetched_at=now,
        )
        # 4. Closed PR (closed 100 days ago, outside 30d, inside 365d)
        pr_closed_365d = Issue(
            project_id=lib_project.id,
            source="github",
            external_id="104",
            issue_type="pull_request",
            title="Older merged PR",
            state="closed",
            author="core-maintainer",
            created_at=now - timedelta(days=120),
            closed_at=now - timedelta(days=100),
            last_fetched_at=now,
        )

        # Seed Issues:
        # 1. Open untriaged issue (created 12 days ago)
        issue_untriaged = Issue(
            project_id=app_project.id,
            source="github",
            external_id="201",
            issue_type="issue",
            title="Bug in build hook",
            state="open",
            author="reporter1",
            created_at=now - timedelta(days=12),
            last_fetched_at=now,
        )
        # 2. Open triaged issue with quick-win score 85
        issue_qw = Issue(
            project_id=lib_project.id,
            source="github",
            external_id="202",
            issue_type="issue",
            title="Typo in docs",
            state="open",
            author="reporter2",
            created_at=now - timedelta(days=8),
            last_fetched_at=now,
        )
        # 3. Closed issue (closed 2 days ago, inside 30d)
        issue_closed = Issue(
            project_id=app_project.id,
            source="github",
            external_id="203",
            issue_type="issue",
            title="Fixed issue",
            state="closed",
            author="reporter3",
            created_at=now - timedelta(days=10),
            closed_at=now - timedelta(days=2),
            last_fetched_at=now,
        )
        test_db_session.add_all(
            [
                pr_contrib,
                pr_maint,
                pr_closed_30d,
                pr_closed_365d,
                issue_untriaged,
                issue_qw,
                issue_closed,
            ]
        )
        await test_db_session.flush()

        # Seed evaluations
        eval_untriaged = LLMEvaluation(
            issue_id=issue_untriaged.id,
            model_name="mock-model",
            suggested_action="needs_triage",
            latest=True,
        )
        eval_qw = LLMEvaluation(
            issue_id=issue_qw.id,
            model_name="mock-model",
            suggested_action="keep_open",
            scores={"quick_win": 85, "impact": 6.5, "complexity": 1.2},
            latest=True,
        )
        eval_pr = LLMEvaluation(
            issue_id=pr_contrib.id,
            model_name="mock-model",
            suggested_action="needs_review",
            latest=True,
        )
        eval_pr_maint = LLMEvaluation(
            issue_id=pr_maint.id,
            model_name="mock-model",
            suggested_action="keep_open",
            latest=True,
        )
        test_db_session.add_all([eval_untriaged, eval_qw, eval_pr, eval_pr_maint])
        await test_db_session.commit()

        config = DashboardConfig(
            initial_release_dates={"debcraft": "2025-06-02T10:36:08-03:00"},
            initial_release_tags={"debcraft": "(unreleased)"},
            hide_prs=["debcraft"],
            hide_releases=["craft-parts"],
        )
        service = DashboardService(test_db_session)

        metrics = await service.get_homepage_metrics(config, now=now)

        # Check PR Velocity & Response
        assert metrics["velocity"]["contributor_count"] == 1
        assert metrics["velocity"]["contributor_avg_age"] == 20
        assert metrics["velocity"]["first_response_waiting_count"] == 1
        assert metrics["velocity"]["first_response_avg_days"] == 20
        assert metrics["velocity"]["overall_count"] == 2
        assert metrics["velocity"]["overall_avg_age"] == 15

        # Check Throughput
        assert metrics["throughput"]["issues_30d"] == 1
        assert metrics["throughput"]["prs_30d"] == 1
        assert metrics["throughput"]["total_30d"] == 2
        assert metrics["throughput"]["issues_365d"] == 1
        assert metrics["throughput"]["prs_365d"] == 2
        assert metrics["throughput"]["total_365d"] == 3

        # Check Untriaged Backlog
        assert metrics["untriaged"]["issues_count"] == 1
        assert metrics["untriaged"]["issues_30d_new"] == 1
        assert metrics["untriaged"]["prs_count"] == 1
        assert metrics["untriaged"]["prs_30d_new"] == 1

        # Check All-Time Volume
        assert metrics["volume"]["open_issues"] == 2
        assert metrics["volume"]["closed_issues"] == 1
        assert metrics["volume"]["open_prs"] == 2
        assert metrics["volume"]["closed_prs"] == 2
        assert metrics["volume"]["total_items"] == 7
        assert metrics["volume"]["total_30d_closed"] == 2

        # Check Least-Recent Apps
        app_names = [a["project_name"] for a in metrics["least_recent_apps"]]
        assert "debcraft" in app_names
        assert "snapcraft" in app_names
        debcraft_spotlight = next(
            a for a in metrics["least_recent_apps"] if a["project_name"] == "debcraft"
        )
        assert debcraft_spotlight["is_fallback"] is True
        assert debcraft_spotlight["version"] == "(unreleased)"
        assert debcraft_spotlight["days_ago"] > 60
        assert debcraft_spotlight["badge_color"] == "red"

        # Check Aging Contributor PRs
        assert len(metrics["aging_prs"]) == 1
        assert metrics["aging_prs"][0]["external_id"] == "101"
        assert metrics["aging_prs"][0]["days_old"] == 20

        # Check Needs Triage Issues
        assert len(metrics["needs_triage_issues"]) == 1
        assert metrics["needs_triage_issues"][0]["external_id"] == "201"

        # Check Quick-Wins Radar
        assert len(metrics["quick_wins"]) == 1
        assert metrics["quick_wins"][0]["external_id"] == "202"
        assert metrics["quick_wins"][0]["quick_win"] == 85

        # Check Project Health directory
        assert len(metrics["application_projects"]) == 2
        assert len(metrics["library_projects"]) == 1

        # Check Cadence (craft-parts is hidden via hide_releases)
        cadence = await service.get_all_repos_release_cadence(config, now=now)
        assert len(cadence) == 2
        # debcraft is unreleased from June 2025 (~480 days ago), should be first
        assert cadence[0]["name"] == "debcraft"
        assert cadence[0]["latest_version"] == "(unreleased)"
        assert cadence[0]["released_at_str"] == "2025-06-02"
