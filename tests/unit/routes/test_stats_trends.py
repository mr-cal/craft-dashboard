"""Unit tests for the /stats/trends/all-data endpoint."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, timedelta

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.models.snapshot import Snapshot
from craft_dashboard.routes.stats import (
    _build_all_projects_aggregate,
    _build_snapshot_dict,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"  # type: ignore[attr-defined]

for idx in LLMEvaluation.__table__.indexes:
    if idx.name == "ix_llm_evaluations_latest_issue":
        idx.dialect_options.pop("postgresql", None)
        if hasattr(idx, "_where_criteria"):
            idx._where_criteria = ()
        break

_TIME_SERIES_KEYS = {
    "dates",
    "open_issues",
    "open_prs",
    "open_issues_external",
    "open_prs_external",
    "open_issues_bots",
    "open_prs_bots",
    "open",
    "open_external",
    "open_internal",
    "open_bots",
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
    "closed_issues",
    "closed_prs",
    "closed_issues_external",
    "closed_prs_external",
    "closed_issues_bots",
    "closed_prs_bots",
    "closed",
    "closed_external",
    "closed_internal",
    "closed_bots",
    "open_bugs",
}

_SNAPSHOT_KEYS = {
    "open_issues",
    "open_prs",
    "nm_open_issues",
    "nm_open_prs",
    "bots_open_issues",
    "bots_open_prs",
    "median_issue_age",
    "median_pr_age",
    "nm_median_issue_age",
    "nm_median_pr_age",
    "median_issue_age_internal",
    "median_pr_age_internal",
    "median_issue_age_bots",
    "median_pr_age_bots",
    "closed_issues_year",
    "closed_prs_year",
    "nm_closed_issues_year",
    "nm_closed_prs_year",
}


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Skip real startup for tests."""
    yield


@pytest.fixture
def test_client(test_db_session: AsyncSession) -> AsyncGenerator[TestClient, None]:
    """Test client wired to the in-memory test session."""
    app = create_app()
    app.router.lifespan_context = _noop_lifespan

    async def _override_session() -> AsyncGenerator[AsyncSession, None]:
        yield test_db_session

    app.dependency_overrides[get_db_session] = _override_session

    with TestClient(app) as client:
        yield client


def _project(name: str, display_order: int) -> Project:
    return Project(
        name=name,
        category="application",
        github_org="canonical",
        display_order=display_order,
    )


def _snapshot(project_id: int, snapshot_date: date, **overrides: int) -> Snapshot:
    return Snapshot(project_id=project_id, snapshot_date=snapshot_date, **overrides)


def _trend_series(
    **overrides: list[int] | list[str],
) -> dict[str, list[int] | list[str]]:
    data = {key: [0] for key in _TIME_SERIES_KEYS if key != "dates"}
    data["dates"] = ["2024-05-20"]
    data.update(overrides)
    return data


class TestBuildAllProjectsAggregateUnit:
    def test_empty_projects_returns_empty(self) -> None:
        """Empty projects dict produces empty aggregate."""
        result = _build_all_projects_aggregate({})
        assert result["dates"] == []
        assert result["open_issues"] == []


class TestTrendsAllDataEmpty:
    def test_empty_db_returns_expected_payload(self, test_client: TestClient) -> None:
        response = test_client.get("/stats/trends/all-data")

        assert response.status_code == 200
        data = response.json()
        assert data["projects"] == {}
        assert data["order"] == []
        assert set(data["snapshot"]) == {"all-projects"}
        assert set(data["snapshot"]["all-projects"]) == _SNAPSHOT_KEYS
        assert all(value == 0 for value in data["snapshot"]["all-projects"].values())


class TestTrendsAllDataSingleProject:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        project = _project("chisel", 20)
        test_db_session.add(project)
        await test_db_session.flush()

        test_db_session.add(
            _snapshot(
                project.id,
                date(2024, 5, 20),
                open_issues=11,
                open_prs=4,
                open_issues_external=6,
                open_issues_internal=4,
                open_prs_external=2,
                open_prs_internal=1,
                open_issues_bots=1,
                open_prs_bots=1,
                open_bugs=3,
                median_issue_age=17,
                median_pr_age=12,
                nm_median_issue_age=13,
                nm_median_pr_age=9,
                median_issue_age_internal=30,
                median_pr_age_internal=21,
                median_issue_age_bots=4,
                median_pr_age_bots=5,
                median_age=15,
                nm_median_age=11,
                median_age_internal=26,
                median_age_bots=4,
                closed_issues=7,
                closed_prs=8,
                closed_issues_external=2,
                closed_issues_internal=4,
                closed_prs_external=3,
                closed_prs_internal=2,
                closed_issues_bots=1,
                closed_prs_bots=2,
            )
        )
        await test_db_session.commit()

    def test_single_project_returns_matching_project_and_all_projects_data(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/stats/trends/all-data")

        assert response.status_code == 200
        data = response.json()

        assert data["order"] == ["chisel"]

        project = data["projects"]["chisel"]
        assert set(project) == _TIME_SERIES_KEYS
        assert project == {
            "dates": ["2024-05-20"],
            "open_issues": [11],
            "open_prs": [4],
            "open_issues_external": [6],
            "open_prs_external": [2],
            "open_issues_bots": [1],
            "open_prs_bots": [1],
            "open": [15],
            "open_external": [8],
            "open_internal": [5],
            "open_bots": [2],
            "median_issue_age": [17],
            "median_pr_age": [12],
            "nm_median_issue_age": [13],
            "nm_median_pr_age": [9],
            "median_issue_age_internal": [30],
            "median_pr_age_internal": [21],
            "median_issue_age_bots": [4],
            "median_pr_age_bots": [5],
            "median_age": [15],
            "nm_median_age": [11],
            "median_age_internal": [26],
            "median_age_bots": [4],
            "closed_issues": [7],
            "closed_prs": [8],
            "closed_issues_external": [2],
            "closed_prs_external": [3],
            "closed_issues_bots": [1],
            "closed_prs_bots": [2],
            "closed": [15],
            "closed_external": [5],
            "closed_internal": [6],
            "closed_bots": [3],
            "open_bugs": [3],
        }
        assert data["projects"]["all-projects"] == project

        expected_snapshot = {
            "open_issues": 11,
            "open_prs": 4,
            "nm_open_issues": 6,
            "nm_open_prs": 2,
            "bots_open_issues": 1,
            "bots_open_prs": 1,
            "median_issue_age": 17,
            "median_pr_age": 12,
            "nm_median_issue_age": 13,
            "nm_median_pr_age": 9,
            "median_issue_age_internal": 30,
            "median_pr_age_internal": 21,
            "median_issue_age_bots": 4,
            "median_pr_age_bots": 5,
            "closed_issues_year": 7,
            "closed_prs_year": 8,
            "nm_closed_issues_year": 2,
            "nm_closed_prs_year": 3,
        }
        assert data["snapshot"]["chisel"] == expected_snapshot
        assert data["snapshot"]["all-projects"] == expected_snapshot


class TestTrendsAllProjectsTimeSeries:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        alpha = _project("alpha", 20)
        beta = _project("beta", 30)
        test_db_session.add_all([alpha, beta])
        await test_db_session.flush()

        same_day = date(2024, 5, 21)
        test_db_session.add_all(
            [
                _snapshot(
                    alpha.id,
                    same_day,
                    open_issues=2,
                    open_prs=3,
                    open_issues_external=1,
                    open_issues_internal=1,
                    open_prs_external=2,
                    open_prs_internal=1,
                    open_issues_bots=1,
                    open_prs_bots=0,
                    open_bugs=4,
                    median_issue_age=10,
                    median_pr_age=14,
                    nm_median_issue_age=6,
                    nm_median_pr_age=8,
                    median_issue_age_internal=20,
                    median_pr_age_internal=30,
                    median_issue_age_bots=2,
                    median_pr_age_bots=4,
                    median_age=12,
                    nm_median_age=7,
                    median_age_internal=25,
                    median_age_bots=2,
                    closed_issues=5,
                    closed_prs=6,
                    closed_issues_external=2,
                    closed_issues_internal=2,
                    closed_prs_external=3,
                    closed_prs_internal=1,
                    closed_issues_bots=1,
                    closed_prs_bots=2,
                ),
                _snapshot(
                    beta.id,
                    same_day,
                    open_issues=5,
                    open_prs=7,
                    open_issues_external=4,
                    open_issues_internal=1,
                    open_prs_external=5,
                    open_prs_internal=2,
                    open_issues_bots=2,
                    open_prs_bots=3,
                    open_bugs=6,
                    median_issue_age=40,
                    median_pr_age=24,
                    nm_median_issue_age=18,
                    nm_median_pr_age=10,
                    median_issue_age_internal=60,
                    median_pr_age_internal=50,
                    median_issue_age_bots=8,
                    median_pr_age_bots=12,
                    median_age=28,
                    nm_median_age=14,
                    median_age_internal=55,
                    median_age_bots=10,
                    closed_issues=8,
                    closed_prs=9,
                    closed_issues_external=4,
                    closed_issues_internal=2,
                    closed_prs_external=5,
                    closed_prs_internal=3,
                    closed_issues_bots=2,
                    closed_prs_bots=1,
                ),
            ]
        )
        await test_db_session.commit()

    def test_all_projects_time_series_sums_scalars_and_averages_medians(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/stats/trends/all-data")

        assert response.status_code == 200
        all_projects = response.json()["projects"]["all-projects"]
        assert all_projects["dates"] == ["2024-05-21"]
        assert all_projects["open_issues"] == [7]
        assert all_projects["open_prs"] == [10]
        assert all_projects["open_issues_external"] == [5]
        assert all_projects["open_prs_external"] == [7]
        assert all_projects["open_issues_bots"] == [3]
        assert all_projects["open_prs_bots"] == [3]
        assert all_projects["open"] == [17]
        assert all_projects["open_external"] == [12]
        assert all_projects["open_internal"] == [5]
        assert all_projects["open_bots"] == [6]
        assert all_projects["closed_issues"] == [13]
        assert all_projects["closed_prs"] == [15]
        assert all_projects["closed_issues_external"] == [6]
        assert all_projects["closed_prs_external"] == [8]
        assert all_projects["closed_issues_bots"] == [3]
        assert all_projects["closed_prs_bots"] == [3]
        assert all_projects["closed"] == [28]
        assert all_projects["closed_external"] == [14]
        assert all_projects["closed_internal"] == [8]
        assert all_projects["closed_bots"] == [6]
        assert all_projects["open_bugs"] == [10]
        assert all_projects["median_issue_age"] == [25]
        assert all_projects["median_pr_age"] == [19]
        assert all_projects["nm_median_issue_age"] == [12]
        assert all_projects["nm_median_pr_age"] == [9]
        assert all_projects["median_issue_age_internal"] == [40]
        assert all_projects["median_pr_age_internal"] == [40]
        assert all_projects["median_issue_age_bots"] == [5]
        assert all_projects["median_pr_age_bots"] == [8]
        assert all_projects["median_age"] == [20]
        assert all_projects["nm_median_age"] == [10]
        assert all_projects["median_age_internal"] == [40]
        assert all_projects["median_age_bots"] == [6]


class TestTrendsProjectOrder:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        third = _project("third", 30)
        first = _project("first", 10)
        second = _project("second", 20)
        test_db_session.add_all([third, first, second])
        await test_db_session.flush()

        snap_day = date(2024, 5, 22)
        test_db_session.add_all(
            [
                _snapshot(third.id, snap_day, open_issues=1),
                _snapshot(first.id, snap_day, open_issues=1),
                _snapshot(second.id, snap_day, open_issues=1),
            ]
        )
        await test_db_session.commit()

    def test_projects_are_ordered_by_display_order(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/stats/trends/all-data")

        assert response.status_code == 200
        data = response.json()
        assert data["order"] == ["first", "second", "third"]
        assert list(data["projects"].keys()) == [
            "first",
            "second",
            "third",
            "all-projects",
        ]


class TestTrendsSnapshotClosedYear:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        project = _project("closer", 10)
        test_db_session.add(project)
        await test_db_session.flush()

        test_db_session.add_all(
            [
                _snapshot(
                    project.id,
                    date(2024, 5, 20),
                    closed_issues=1,
                    closed_prs=4,
                    closed_issues_external=2,
                    closed_prs_external=3,
                ),
                _snapshot(
                    project.id,
                    date(2024, 5, 21),
                    closed_issues=2,
                    closed_prs=5,
                    closed_issues_external=3,
                    closed_prs_external=4,
                ),
                _snapshot(
                    project.id,
                    date(2024, 5, 22),
                    closed_issues=3,
                    closed_prs=6,
                    closed_issues_external=4,
                    closed_prs_external=5,
                ),
            ]
        )
        await test_db_session.commit()

    def test_snapshot_closed_year_sums_the_full_time_series(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/stats/trends/all-data")

        assert response.status_code == 200
        snapshot = response.json()["snapshot"]
        assert snapshot["closer"]["closed_issues_year"] == 6
        assert snapshot["closer"]["closed_prs_year"] == 15
        assert snapshot["closer"]["nm_closed_issues_year"] == 9
        assert snapshot["closer"]["nm_closed_prs_year"] == 12
        assert snapshot["all-projects"]["closed_issues_year"] == 6
        assert snapshot["all-projects"]["closed_prs_year"] == 15


class TestTrendsClosedYearBug:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        project = _project("year-bug", 10)
        test_db_session.add(project)
        await test_db_session.flush()

        start_day = date(2023, 1, 1)
        test_db_session.add_all(
            [
                _snapshot(
                    project.id,
                    start_day + timedelta(days=offset),
                    closed_issues=1,
                )
                for offset in range(400)
            ]
        )
        await test_db_session.commit()

    def test_snapshot_closed_issues_year_uses_last_365_entries(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/stats/trends/all-data")

        assert response.status_code == 200
        assert response.json()["snapshot"]["year-bug"]["closed_issues_year"] == 365


class TestBuildAllProjectsAggregate:
    def test_build_all_projects_aggregate_sums_scalars_and_averages_medians(
        self,
    ) -> None:
        projects = {
            "alpha": _trend_series(
                dates=["2024-05-20", "2024-05-21"],
                open_issues=[1, 2],
                open_prs=[3, 4],
                open_issues_external=[5, 6],
                open_prs_external=[7, 8],
                open_issues_bots=[1, 0],
                open_prs_bots=[0, 1],
                open=[4, 6],
                open_external=[12, 14],
                open_internal=[2, 3],
                open_bots=[1, 1],
                median_issue_age=[10, 20],
                median_pr_age=[30, 40],
                nm_median_issue_age=[11, 21],
                nm_median_pr_age=[31, 41],
                median_issue_age_internal=[12, 22],
                median_pr_age_internal=[32, 42],
                median_issue_age_bots=[0, 23],
                median_pr_age_bots=[33, 43],
                median_age=[15, 25],
                nm_median_age=[16, 26],
                median_age_internal=[17, 27],
                median_age_bots=[0, 28],
                closed_issues=[9, 10],
                closed_prs=[11, 12],
                closed_issues_external=[13, 14],
                closed_prs_external=[15, 16],
                closed_issues_bots=[1, 2],
                closed_prs_bots=[3, 4],
                closed=[20, 22],
                closed_external=[28, 30],
                closed_internal=[18, 19],
                closed_bots=[4, 6],
                open_bugs=[5, 6],
            ),
            "beta": _trend_series(
                dates=["2024-05-21", "2024-05-22"],
                open_issues=[7, 8],
                open_prs=[9, 10],
                open_issues_external=[11, 12],
                open_prs_external=[13, 14],
                open_issues_bots=[2, 3],
                open_prs_bots=[4, 5],
                open=[16, 18],
                open_external=[24, 26],
                open_internal=[9, 10],
                open_bots=[6, 8],
                median_issue_age=[50, 60],
                median_pr_age=[70, 80],
                nm_median_issue_age=[51, 61],
                nm_median_pr_age=[71, 81],
                median_issue_age_internal=[52, 62],
                median_pr_age_internal=[72, 82],
                median_issue_age_bots=[53, 63],
                median_pr_age_bots=[73, 83],
                median_age=[55, 65],
                nm_median_age=[56, 66],
                median_age_internal=[57, 67],
                median_age_bots=[58, 68],
                closed_issues=[17, 18],
                closed_prs=[19, 20],
                closed_issues_external=[21, 22],
                closed_prs_external=[23, 24],
                closed_issues_bots=[5, 6],
                closed_prs_bots=[7, 8],
                closed=[36, 38],
                closed_external=[44, 46],
                closed_internal=[25, 26],
                closed_bots=[12, 14],
                open_bugs=[9, 10],
            ),
        }

        assert _build_all_projects_aggregate(projects) == {
            "dates": ["2024-05-20", "2024-05-21", "2024-05-22"],
            "open_issues": [1, 9, 8],
            "open_prs": [3, 13, 10],
            "open_issues_external": [5, 17, 12],
            "open_prs_external": [7, 21, 14],
            "open_issues_bots": [1, 2, 3],
            "open_prs_bots": [0, 5, 5],
            "open": [4, 22, 18],
            "open_external": [12, 38, 26],
            "open_internal": [2, 12, 10],
            "open_bots": [1, 7, 8],
            "closed_issues": [9, 27, 18],
            "closed_prs": [11, 31, 20],
            "closed_issues_external": [13, 35, 22],
            "closed_prs_external": [15, 39, 24],
            "closed_issues_bots": [1, 7, 6],
            "closed_prs_bots": [3, 11, 8],
            "closed": [20, 58, 38],
            "closed_external": [28, 74, 46],
            "closed_internal": [18, 44, 26],
            "closed_bots": [4, 18, 14],
            "open_bugs": [5, 15, 10],
            "median_issue_age": [10, 35, 60],
            "median_pr_age": [30, 55, 80],
            "nm_median_issue_age": [11, 36, 61],
            "nm_median_pr_age": [31, 56, 81],
            "median_issue_age_internal": [12, 37, 62],
            "median_pr_age_internal": [32, 57, 82],
            "median_issue_age_bots": [0, 38, 63],
            "median_pr_age_bots": [33, 58, 83],
            "median_age": [15, 40, 65],
            "nm_median_age": [16, 41, 66],
            "median_age_internal": [17, 42, 67],
            "median_age_bots": [0, 43, 68],
        }


class TestBuildSnapshotDict:
    def test_build_snapshot_dict_uses_latest_values_and_aggregate_snapshot(
        self,
    ) -> None:
        projects = {
            "alpha": _trend_series(
                dates=["2024-05-20", "2024-05-21"],
                open_issues=[1, 3],
                open_prs=[2, 4],
                open_issues_external=[5, 7],
                open_prs_external=[6, 8],
                open_issues_bots=[0, 1],
                open_prs_bots=[1, 2],
                open=[3, 7],
                open_external=[11, 15],
                open_internal=[2, 3],
                open_bots=[1, 3],
                median_issue_age=[10, 30],
                median_pr_age=[20, 40],
                nm_median_issue_age=[11, 31],
                nm_median_pr_age=[21, 41],
                median_issue_age_internal=[12, 32],
                median_pr_age_internal=[22, 42],
                median_issue_age_bots=[13, 33],
                median_pr_age_bots=[23, 43],
                median_age=[15, 35],
                nm_median_age=[16, 36],
                median_age_internal=[17, 37],
                median_age_bots=[18, 38],
                closed_issues=[9, 10],
                closed_prs=[11, 12],
                closed_issues_external=[13, 14],
                closed_prs_external=[15, 16],
                closed=[20, 22],
                closed_external=[28, 30],
                closed_internal=[19, 20],
                closed_bots=[4, 6],
            ),
            "beta": _trend_series(
                open_issues=[4],
                open_prs=[5],
                open_issues_external=[6],
                open_prs_external=[7],
                open_issues_bots=[2],
                open_prs_bots=[3],
                open=[9],
                open_external=[13],
                open_internal=[4],
                open_bots=[5],
                median_issue_age=[50],
                median_pr_age=[60],
                nm_median_issue_age=[51],
                nm_median_pr_age=[61],
                median_issue_age_internal=[52],
                median_pr_age_internal=[62],
                median_issue_age_bots=[53],
                median_pr_age_bots=[63],
                median_age=[55],
                nm_median_age=[56],
                median_age_internal=[57],
                median_age_bots=[58],
                closed_issues=[20],
                closed_prs=[21],
                closed_issues_external=[22],
                closed_prs_external=[23],
                closed=[41],
                closed_external=[45],
                closed_internal=[24],
                closed_bots=[7],
            ),
            "all-projects": _trend_series(open_issues=[999]),
        }

        snapshot = _build_snapshot_dict(projects)

        assert snapshot == {
            "alpha": {
                "open_issues": 3,
                "open_prs": 4,
                "nm_open_issues": 7,
                "nm_open_prs": 8,
                "bots_open_issues": 1,
                "bots_open_prs": 2,
                "median_issue_age": 30,
                "median_pr_age": 40,
                "nm_median_issue_age": 31,
                "nm_median_pr_age": 41,
                "median_issue_age_internal": 32,
                "median_pr_age_internal": 42,
                "median_issue_age_bots": 33,
                "median_pr_age_bots": 43,
                "closed_issues_year": 19,
                "closed_prs_year": 23,
                "nm_closed_issues_year": 27,
                "nm_closed_prs_year": 31,
            },
            "beta": {
                "open_issues": 4,
                "open_prs": 5,
                "nm_open_issues": 6,
                "nm_open_prs": 7,
                "bots_open_issues": 2,
                "bots_open_prs": 3,
                "median_issue_age": 50,
                "median_pr_age": 60,
                "nm_median_issue_age": 51,
                "nm_median_pr_age": 61,
                "median_issue_age_internal": 52,
                "median_pr_age_internal": 62,
                "median_issue_age_bots": 53,
                "median_pr_age_bots": 63,
                "closed_issues_year": 20,
                "closed_prs_year": 21,
                "nm_closed_issues_year": 22,
                "nm_closed_prs_year": 23,
            },
            "all-projects": {
                "open_issues": 7,
                "open_prs": 9,
                "nm_open_issues": 13,
                "nm_open_prs": 15,
                "bots_open_issues": 3,
                "bots_open_prs": 5,
                "median_issue_age": 40,
                "median_pr_age": 50,
                "nm_median_issue_age": 41,
                "nm_median_pr_age": 51,
                "median_issue_age_internal": 42,
                "median_pr_age_internal": 52,
                "median_issue_age_bots": 43,
                "median_pr_age_bots": 53,
                "closed_issues_year": 39,
                "closed_prs_year": 44,
                "nm_closed_issues_year": 49,
                "nm_closed_prs_year": 54,
            },
        }


class TestTrendsPerKeyMedianDenominator:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        internal = _project("internal-only", 10)
        bots = _project("bots-only", 20)
        test_db_session.add_all([internal, bots])
        await test_db_session.flush()

        snap_day = date(2024, 5, 22)
        test_db_session.add_all(
            [
                _snapshot(
                    internal.id,
                    snap_day,
                    median_issue_age_internal=20,
                    median_pr_age_internal=10,
                ),
                _snapshot(
                    bots.id,
                    snap_day,
                    median_issue_age_bots=40,
                    median_pr_age_bots=18,
                ),
            ]
        )
        await test_db_session.commit()

    def test_all_projects_snapshot_uses_per_key_median_denominators(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/stats/trends/all-data")

        assert response.status_code == 200
        snapshot = response.json()["snapshot"]["all-projects"]
        assert snapshot["median_issue_age_internal"] == 20
        assert snapshot["median_pr_age_internal"] == 10
        assert snapshot["median_issue_age_bots"] == 40
        assert snapshot["median_pr_age_bots"] == 18


class TestTrendsAllProjectsMedianZeroExcluded:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        with_age = _project("with-age", 10)
        zero_age = _project("zero-age", 20)
        test_db_session.add_all([with_age, zero_age])
        await test_db_session.flush()

        snap_day = date(2024, 5, 22)
        test_db_session.add_all(
            [
                _snapshot(with_age.id, snap_day, median_issue_age=30, median_pr_age=12),
                _snapshot(zero_age.id, snap_day, median_issue_age=0, median_pr_age=0),
            ]
        )
        await test_db_session.commit()

    def test_zero_median_values_are_excluded_from_averages(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/stats/trends/all-data")

        assert response.status_code == 200
        data = response.json()
        assert data["projects"]["all-projects"]["median_issue_age"] == [30]
        assert data["projects"]["all-projects"]["median_pr_age"] == [12]
        assert data["snapshot"]["all-projects"]["median_issue_age"] == 30
        assert data["snapshot"]["all-projects"]["median_pr_age"] == 12


class TestTrendsCombinedSeries:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        project = _project("combined", 10)
        test_db_session.add(project)
        await test_db_session.flush()

        test_db_session.add(
            _snapshot(
                project.id,
                date(2024, 5, 22),
                open_issues=4,
                open_prs=6,
                open_issues_external=2,
                open_issues_internal=1,
                open_prs_external=3,
                open_prs_internal=2,
                open_issues_bots=1,
                open_prs_bots=1,
                median_age=18,
                nm_median_age=12,
                median_age_internal=20,
                median_age_bots=8,
                closed_issues=5,
                closed_prs=7,
                closed_issues_external=2,
                closed_issues_internal=1,
                closed_prs_external=3,
                closed_prs_internal=2,
                closed_issues_bots=1,
                closed_prs_bots=2,
            )
        )
        await test_db_session.commit()

    def test_response_includes_combined_open_closed_and_median_age_series(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/stats/trends/all-data")

        assert response.status_code == 200
        project = response.json()["projects"]["combined"]
        assert project["open"] == [10]
        assert project["open_external"] == [5]
        assert project["open_internal"] == [3]
        assert project["open_bots"] == [2]
        assert project["median_age"] == [18]
        assert project["nm_median_age"] == [12]
        assert project["median_age_internal"] == [20]
        assert project["median_age_bots"] == [8]
        assert project["closed"] == [12]
        assert project["closed_external"] == [5]
        assert project["closed_internal"] == [3]
        assert project["closed_bots"] == [3]
