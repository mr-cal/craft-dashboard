"""Integration tests for dashboard and issues routes with real DB data."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.app import create_app
from craft_dashboard.config import DashboardConfig
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project

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


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


@pytest.fixture
def test_client(test_db_session: AsyncSession) -> TestClient:
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.state.config = DashboardConfig()

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield test_db_session

    app.dependency_overrides[get_db_session] = _override
    with TestClient(app) as client:
        yield client


async def _seed_project_with_issues(test_db_session: AsyncSession) -> Project:
    project = Project(name="snapcraft", category="application", github_org="canonical")
    test_db_session.add(project)
    await test_db_session.flush()

    test_db_session.add_all(
        [
            Issue(
                project_id=project.id,
                source="github",
                external_id="1",
                issue_type="issue",
                title="first dashboard issue",
                state="open",
                author="alice",
                labels=[],
                last_fetched_at=datetime.now(tz=UTC),
            ),
            Issue(
                project_id=project.id,
                source="github",
                external_id="2",
                issue_type="pull_request",
                title="open dashboard pr",
                state="open",
                author="bob",
                labels=[],
                last_fetched_at=datetime.now(tz=UTC),
            ),
            Issue(
                project_id=project.id,
                source="github",
                external_id="3",
                issue_type="issue",
                title="closed dashboard issue",
                state="closed",
                author="carol",
                labels=[],
                last_fetched_at=datetime.now(tz=UTC),
            ),
        ]
    )
    await test_db_session.commit()
    return project


class TestDashboardWithData:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        await _seed_project_with_issues(test_db_session)

    def test_dashboard_shows_project_name(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/")

        assert response.status_code == 200
        assert "snapcraft" in response.text

    def test_dashboard_empty_db(self, test_client: TestClient) -> None:
        response = test_client.get("/")

        assert response.status_code == 200


class TestIssuesPageWithData:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        await _seed_project_with_issues(test_db_session)

    def test_issues_page_shows_issue_title(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/issues")

        assert response.status_code == 200
        assert "first dashboard issue" in response.text

    def test_issues_table_partial(self, test_client: TestClient, seeded: None) -> None:
        response = test_client.get("/issues/table")

        assert response.status_code == 200
        assert "first dashboard issue" in response.text

    def test_issues_filter_by_project(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/issues", params={"project": "snapcraft"})

        assert response.status_code == 200
        assert "first dashboard issue" in response.text

    def test_issues_filter_nonexistent(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/issues", params={"project": "nonexistent"})

        assert response.status_code == 200
        assert "first dashboard issue" not in response.text
        assert "No issues found matching the current filters." in response.text

    def test_issues_empty_db(self, test_client: TestClient) -> None:
        response = test_client.get("/issues")

        assert response.status_code == 200
