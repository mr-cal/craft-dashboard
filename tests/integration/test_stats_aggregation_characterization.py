"""Characterization tests for stats trend aggregation behaviour."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from typing import TYPE_CHECKING

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.project import Project
from craft_dashboard.models.snapshot import Snapshot
from craft_dashboard.routes.stats import _build_all_projects_aggregate
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Skip real startup so tests use only the in-memory database."""
    yield


@pytest.fixture
def test_client(test_db_session: AsyncSession) -> AsyncGenerator[TestClient, None]:
    app = create_app()
    app.router.lifespan_context = _noop_lifespan

    async def _override_session() -> AsyncGenerator[AsyncSession, None]:
        yield test_db_session

    app.dependency_overrides[get_db_session] = _override_session

    with TestClient(app) as client:
        yield client


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


def _snapshot(project_id: int, snapshot_date: date, **overrides: int) -> Snapshot:
    return Snapshot(project_id=project_id, snapshot_date=snapshot_date, **overrides)


def _series(**overrides: list[int] | list[str]) -> dict[str, list[int] | list[str]]:
    keys = [
        "open_issues",
        "open_prs",
        "open_issues_external",
        "open_prs_external",
        "open_issues_internal",
        "open_prs_internal",
        "open_issues_bots",
        "open_prs_bots",
        "open",
        "open_external",
        "open_internal",
        "open_bots",
        "closed_issues",
        "closed_prs",
        "closed_issues_external",
        "closed_prs_external",
        "closed_issues_internal",
        "closed_prs_internal",
        "closed_issues_bots",
        "closed_prs_bots",
        "closed",
        "closed_external",
        "closed_internal",
        "closed_bots",
        "open_bugs",
        "median_issue_age",
        "median_pr_age",
        "nm_median_issue_age",
        "nm_median_pr_age",
        "median_issue_age_internal",
        "median_pr_age_internal",
        "median_issue_age_bots",
        "median_pr_age_bots",
        "median_age",
        "nm_median_age",
        "median_age_internal",
        "median_age_bots",
    ]
    data: dict[str, list[int] | list[str]] = {key: [0] for key in keys}
    data["dates"] = ["2024-06-01"]
    data.update(overrides)
    return data


class TestAllProjectsAggregateCharacterization:
    def test_missing_project_dates_forward_fill_open_counts_but_not_closed_counts(
        self,
    ) -> None:
        projects = {
            "alpha": _series(
                dates=["2024-06-01"],
                open_issues=[10],
                closed_issues=[3],
                median_issue_age=[10],
            ),
            "beta": _series(
                dates=["2024-06-02"],
                open_issues=[5],
                closed_issues=[7],
                median_issue_age=[20],
            ),
        }

        result = _build_all_projects_aggregate(projects)

        assert result["dates"] == ["2024-06-01", "2024-06-02"]
        assert result["open_issues"] == [10, 15]
        assert result["closed_issues"] == [3, 7]
        assert result["median_issue_age"] == [10, 20]

    def test_weighted_median_fallback_truncates_fractional_averages(self) -> None:
        projects = {
            "alpha": _series(open_issues=[2], median_issue_age=[10]),
            "beta": _series(open_issues=[1], median_issue_age=[11]),
        }

        result = _build_all_projects_aggregate(projects)

        assert result["median_issue_age"] == [10]

    def test_db_all_projects_rows_override_only_medians_not_scalar_rollups(
        self,
    ) -> None:
        projects = {
            "alpha": _series(open_issues=[2], median_issue_age=[10]),
            "beta": _series(open_issues=[3], median_issue_age=[20]),
        }
        db_medians = _series(open_issues=[999], median_issue_age=[77])

        result = _build_all_projects_aggregate(projects, db_medians=db_medians)

        assert result["open_issues"] == [5]
        assert result["median_issue_age"] == [77]


class TestTrendsEndpointCharacterization:
    @pytest.fixture
    async def aggregate_seed(self, test_db_session: AsyncSession) -> None:
        app = _project("app", display_order=20)
        rollup = _project("rollup", category="aggregate", display_order=10)
        db_all = _project("all-projects", category="aggregate", display_order=30)
        test_db_session.add_all([app, rollup, db_all])
        await test_db_session.flush()
        snapshot_day = date(2024, 6, 1)
        test_db_session.add_all(
            [
                _snapshot(
                    app.id,
                    snapshot_day,
                    open_issues=2,
                    closed_issues=1,
                    median_issue_age=10,
                ),
                _snapshot(
                    rollup.id,
                    snapshot_day,
                    open_issues=5,
                    closed_issues=4,
                    median_issue_age=50,
                ),
                _snapshot(
                    db_all.id,
                    snapshot_day,
                    open_issues=999,
                    closed_issues=999,
                    median_issue_age=77,
                ),
            ]
        )
        await test_db_session.commit()

    def test_trends_all_data_includes_aggregate_category_except_all_projects_name(
        self,
        test_client: TestClient,
        aggregate_seed: None,
    ) -> None:
        response = test_client.get("/stats/trends/all-data")

        assert response.status_code == 200
        data = response.json()
        assert data["order"] == ["rollup", "app"]
        assert sorted(data["projects"]) == ["all-projects", "app", "rollup"]
        assert data["projects"]["all-projects"]["open_issues"] == [7]
        assert data["projects"]["all-projects"]["closed_issues"] == [5]
        assert data["projects"]["all-projects"]["median_issue_age"] == [77]
        assert data["snapshot"]["all-projects"]["open_issues"] == 7
        # NOTE: looks suspect -- aggregate-category project snapshots are included
        # in the all-projects rollup unless the project is literally named
        # "all-projects".
        assert data["snapshot"]["all-projects"]["median_issue_age"] == 30

    @pytest.fixture
    async def empty_project_seed(self, test_db_session: AsyncSession) -> None:
        test_db_session.add(_project("empty-project"))
        await test_db_session.commit()

    def test_trends_data_for_project_with_no_snapshots_returns_empty_series(
        self,
        test_client: TestClient,
        empty_project_seed: None,
    ) -> None:
        response = test_client.get("/stats/trends/data?project=empty-project")

        assert response.status_code == 200
        assert response.json() == {
            "labels": [],
            "datasets": [
                {"label": "Open Issues", "data": [], "borderColor": "#4e79a7"},
                {"label": "Open PRs", "data": [], "borderColor": "#f28e2b"},
                {"label": "Open Bugs", "data": [], "borderColor": "#e15759"},
            ],
        }
