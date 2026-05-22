"""Unit tests for the /stats/trends/all-data endpoint."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.models.snapshot import Snapshot

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
    "median_issue_age",
    "median_pr_age",
    "nm_median_issue_age",
    "nm_median_pr_age",
    "median_issue_age_internal",
    "median_pr_age_internal",
    "median_issue_age_bots",
    "median_pr_age_bots",
    "closed_issues",
    "closed_prs",
    "closed_issues_external",
    "closed_prs_external",
    "closed_issues_bots",
    "closed_prs_bots",
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
                open_prs_external=2,
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
                closed_issues=7,
                closed_prs=8,
                closed_issues_external=2,
                closed_prs_external=3,
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
            "median_issue_age": [17],
            "median_pr_age": [12],
            "nm_median_issue_age": [13],
            "nm_median_pr_age": [9],
            "median_issue_age_internal": [30],
            "median_pr_age_internal": [21],
            "median_issue_age_bots": [4],
            "median_pr_age_bots": [5],
            "closed_issues": [7],
            "closed_prs": [8],
            "closed_issues_external": [2],
            "closed_prs_external": [3],
            "closed_issues_bots": [1],
            "closed_prs_bots": [2],
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
                    open_prs_external=2,
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
                    closed_issues=5,
                    closed_prs=6,
                    closed_issues_external=2,
                    closed_prs_external=3,
                    closed_issues_bots=1,
                    closed_prs_bots=2,
                ),
                _snapshot(
                    beta.id,
                    same_day,
                    open_issues=5,
                    open_prs=7,
                    open_issues_external=4,
                    open_prs_external=5,
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
                    closed_issues=8,
                    closed_prs=9,
                    closed_issues_external=4,
                    closed_prs_external=5,
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
        assert all_projects["closed_issues"] == [13]
        assert all_projects["closed_prs"] == [15]
        assert all_projects["closed_issues_external"] == [6]
        assert all_projects["closed_prs_external"] == [8]
        assert all_projects["closed_issues_bots"] == [3]
        assert all_projects["closed_prs_bots"] == [3]
        assert all_projects["open_bugs"] == [10]
        assert all_projects["median_issue_age"] == [25]
        assert all_projects["median_pr_age"] == [19]
        assert all_projects["nm_median_issue_age"] == [12]
        assert all_projects["nm_median_pr_age"] == [9]
        assert all_projects["median_issue_age_internal"] == [40]
        assert all_projects["median_pr_age_internal"] == [40]
        assert all_projects["median_issue_age_bots"] == [5]
        assert all_projects["median_pr_age_bots"] == [8]


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
        assert list(data["projects"].keys()) == ["first", "second", "third", "all-projects"]


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
