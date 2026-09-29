"""Characterization tests for dashboard stats service aggregation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from craft_dashboard.config import DashboardConfig
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.project import Project
from craft_dashboard.models.release import Release
from craft_dashboard.services.dashboard_service import DashboardService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _config(**overrides: Any) -> DashboardConfig:
    return DashboardConfig(**overrides)


def _project(
    name: str,
    *,
    category: str = "application",
    display_order: int = 10,
) -> Project:
    return Project(
        name=name,
        category=category,
        github_org="canonical",
        display_order=display_order,
    )


def _issue(
    project_id: int,
    external_id: str,
    *,
    issue_type: str = "issue",
    state: str = "open",
    created_at: datetime | None = None,
    closed_at: datetime | None = None,
    author_is_maintainer: bool = False,
    author_is_bot: bool = False,
    metadata_: dict[str, Any] | None = None,
    comments: list[dict[str, Any]] | None = None,
) -> Issue:
    now = datetime(2024, 6, 1, 12, tzinfo=UTC)
    return Issue(
        project_id=project_id,
        source="github",
        external_id=external_id,
        issue_type=issue_type,
        title=f"Issue {external_id}",
        body="",
        state=state,
        author="author",
        author_is_maintainer=author_is_maintainer,
        author_is_bot=author_is_bot,
        labels=[],
        created_at=created_at or now,
        updated_at=now,
        closed_at=closed_at,
        url=f"https://example.test/{external_id}",
        metadata_=metadata_ or {},
        comments=comments or [],
        last_fetched_at=now,
    )


def _release(
    project_id: int,
    *,
    version: str,
    branch: str,
    released_at: datetime | None,
    metadata_: dict[str, Any] | None = None,
) -> Release:
    return Release(
        project_id=project_id,
        version=version,
        branch=branch,
        released_at=released_at,
        is_hotfix=False,
        metadata_=metadata_ or {},
    )


async def test_homepage_metrics_empty_dataset_returns_zero_none_and_empty_lists(
    test_db_session: AsyncSession,
) -> None:
    service = DashboardService(test_db_session)

    result = await service.get_homepage_metrics(
        _config(),
        now=datetime(2024, 6, 1, 12, tzinfo=UTC),
    )

    assert result["project_count"] == 0
    assert result["velocity"] == {
        "contributor_count": 0,
        "contributor_avg_age": None,
        "contributor_avg_delta": None,
        "contributor_avg_baseline": None,
        "first_response_waiting_count": 0,
        "first_response_avg_days": None,
        "first_response_avg_delta": None,
        "first_response_avg_baseline": None,
        "overall_count": 0,
        "overall_avg_age": None,
        "overall_avg_delta": None,
        "overall_avg_baseline": None,
    }
    assert result["throughput"] == {
        "issues_30d": 0,
        "prs_30d": 0,
        "total_30d": 0,
        "issues_365d": 0,
        "prs_365d": 0,
        "total_365d": 0,
        "total_monthly_avg": 0,
        "total_monthly_delta": 0,
    }
    assert result["untriaged"] == {
        "issues_count": 0,
        "issues_30d_new": 0,
        "prs_count": 0,
        "prs_30d_new": 0,
    }
    assert result["volume"]["total_items"] == 0
    assert result["least_recent_apps"] == []
    assert result["application_projects"] == []


async def test_homepage_uses_inclusive_30_and_365_day_cutoffs(
    test_db_session: AsyncSession,
) -> None:
    now = datetime(2024, 6, 1, 12, tzinfo=UTC)
    project = _project("app")
    test_db_session.add(project)
    await test_db_session.flush()
    test_db_session.add_all(
        [
            _issue(
                project.id,
                "closed-at-30d-cutoff",
                state="closed",
                created_at=now - timedelta(days=31),
                closed_at=now - timedelta(days=30),
            ),
            _issue(
                project.id,
                "closed-before-30d-cutoff",
                state="closed",
                created_at=now - timedelta(days=32),
                closed_at=now - timedelta(days=30, seconds=1),
            ),
            _issue(
                project.id,
                "pr-at-365d-cutoff",
                issue_type="pull_request",
                state="merged",
                created_at=now - timedelta(days=366),
                closed_at=now - timedelta(days=365),
            ),
        ]
    )
    await test_db_session.commit()

    result = await DashboardService(test_db_session).get_homepage_metrics(
        _config(), now=now
    )

    assert result["throughput"]["issues_30d"] == 1
    assert result["throughput"]["prs_30d"] == 0
    assert result["throughput"]["issues_365d"] == 2
    assert result["throughput"]["prs_365d"] == 1
    assert result["throughput"]["total_monthly_avg"] == 0
    # NOTE: looks suspect -- Python's bankers rounding makes 3 / 12 round to 0.
    assert result["throughput"]["total_monthly_delta"] == 1


async def test_homepage_treats_naive_now_as_utc_and_clamps_future_release_age(
    test_db_session: AsyncSession,
) -> None:
    project = _project("app")
    test_db_session.add(project)
    await test_db_session.flush()
    test_db_session.add(
        _release(
            project.id,
            version="future",
            branch="stable",
            released_at=datetime(2024, 6, 2, 12),
        )
    )
    await test_db_session.commit()

    result = await DashboardService(test_db_session).get_homepage_metrics(
        _config(),
        now=datetime(2024, 6, 1, 12),
    )

    assert result["least_recent_apps"] == [
        {
            "project_name": "app",
            "version": "future",
            "released_at": datetime(2024, 6, 2, 12),
            "days_ago": 0,
            "badge_color": "green",
            "is_fallback": False,
        }
    ]


async def test_homepage_excludes_aggregate_category_from_counts_and_releases(
    test_db_session: AsyncSession,
) -> None:
    now = datetime(2024, 6, 1, 12, tzinfo=UTC)
    app = _project("app", category="application")
    aggregate = _project("rollup", category="aggregate")
    test_db_session.add_all([app, aggregate])
    await test_db_session.flush()
    test_db_session.add_all(
        [
            _issue(app.id, "1", state="open", created_at=now - timedelta(days=1)),
            _issue(
                aggregate.id,
                "2",
                state="open",
                created_at=now - timedelta(days=1),
            ),
            _release(
                aggregate.id,
                version="ignored",
                branch="stable",
                released_at=now - timedelta(days=10),
            ),
        ]
    )
    await test_db_session.commit()

    result = await DashboardService(test_db_session).get_homepage_metrics(
        _config(), now=now
    )

    assert result["project_count"] == 1
    assert result["volume"]["open_issues"] == 1
    assert [row["project_name"] for row in result["least_recent_apps"]] == ["app"]
    assert result["least_recent_apps"][0]["version"] == "(unreleased)"


async def test_release_cadence_chooses_first_inserted_release_when_dates_tie(
    test_db_session: AsyncSession,
) -> None:
    now = datetime(2024, 6, 1, 12, tzinfo=UTC)
    project = _project("app")
    test_db_session.add(project)
    await test_db_session.flush()
    tied_date = datetime(2024, 5, 1, tzinfo=UTC)
    test_db_session.add_all(
        [
            _release(
                project.id,
                version="inserted-first",
                branch="stable",
                released_at=tied_date,
            ),
            _release(
                project.id,
                version="inserted-second",
                branch="edge",
                released_at=tied_date,
            ),
        ]
    )
    await test_db_session.commit()

    rows = await DashboardService(test_db_session).get_all_repos_release_cadence(
        _config(), now=now
    )

    # NOTE: looks suspect -- latest release ordering has no explicit tie-breaker;
    # SQLite currently returns the first inserted row for equal released_at values.
    assert rows[0]["latest_version"] == "inserted-first"


async def test_release_cadence_null_release_uses_fallback_and_null_days_sort_last(
    test_db_session: AsyncSession,
) -> None:
    now = datetime(2024, 6, 1, 12, tzinfo=UTC)
    with_fallback = _project("with-fallback", display_order=10)
    with_null_only = _project("with-null-only", display_order=20)
    with_old_release = _project("with-old-release", display_order=30)
    test_db_session.add_all([with_fallback, with_null_only, with_old_release])
    await test_db_session.flush()
    test_db_session.add_all(
        [
            _release(
                with_fallback.id,
                version="db-null-version",
                branch="stable",
                released_at=None,
            ),
            _release(
                with_null_only.id,
                version="null-only-version",
                branch="stable",
                released_at=None,
            ),
            _release(
                with_old_release.id,
                version="old-version",
                branch="stable",
                released_at=now - timedelta(days=40),
                metadata_={"commits_since_tag": 9},
            ),
        ]
    )
    await test_db_session.commit()

    rows = await DashboardService(test_db_session).get_all_repos_release_cadence(
        _config(
            initial_release_dates={"with-fallback": "2024-05-15"},
            initial_release_tags={"with-fallback": "fallback-tag"},
        ),
        now=now,
    )

    assert [row["name"] for row in rows] == [
        "with-old-release",
        "with-fallback",
        "with-null-only",
    ]
    assert rows[0]["latest_version"] == "old-version"
    assert rows[0]["days_ago"] == 40
    assert rows[0]["commits_since"] == 9
    assert rows[1]["latest_version"] == "fallback-tag"
    assert rows[1]["days_ago"] == 17
    assert rows[1]["is_fallback"] is False
    assert rows[2]["latest_version"] == "(unreleased)"
    assert rows[2]["released_at"] is None
    assert rows[2]["days_ago"] is None
    assert rows[2]["badge_color"] == "neutral"
