"""Integration tests for dashboard and issues routes with real DB data."""

import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.config import DashboardConfig
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from fastapi import FastAPI
from fastapi.testclient import TestClient
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


class TestDashboardExcludesAggregate:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        test_db_session.add_all([
            Project(name="snapcraft", category="application", github_org="canonical"),
            Project(name="craft-parts", category="library", github_org="canonical"),
            Project(
                name="all-projects",
                category="aggregate",
                github_org="canonical",
                display_order=-1,
            ),
        ])
        await test_db_session.commit()

    def test_project_count_excludes_aggregate(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/")
        assert response.status_code == 200
        assert "all-projects" not in response.text

    def test_aggregate_not_in_tables(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/")
        assert response.status_code == 200
        assert "all-projects" not in response.text


class TestIssueNumberSort:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        project = Project(
            name="snapcraft", category="application", github_org="canonical"
        )
        test_db_session.add(project)
        await test_db_session.flush()

        for eid in ["1", "2", "10", "20", "3"]:
            test_db_session.add(
                Issue(
                    project_id=project.id,
                    source="github",
                    external_id=eid,
                    issue_type="issue",
                    title=f"Issue {eid}",
                    state="open",
                    labels=[],
                    last_fetched_at=datetime.now(tz=UTC),
                )
            )
        await test_db_session.commit()

    def test_sort_by_number_is_numeric(
        self, test_client: TestClient, seeded: None
    ) -> None:
        """Sorting by number should be numeric, not lexicographic."""
        response = test_client.get("/issues", params={"sort": "number"})
        assert response.status_code == 200
        text = response.text
        pos_1 = re.search(r"snapcraft#1(?!\\d)", text)
        pos_2 = re.search(r"snapcraft#2(?!\\d)", text)
        pos_3 = re.search(r"snapcraft#3(?!\\d)", text)
        pos_10 = re.search(r"snapcraft#10(?!\\d)", text)
        pos_20 = re.search(r"snapcraft#20(?!\\d)", text)
        assert all(match is not None for match in (pos_1, pos_2, pos_3, pos_10, pos_20))
        assert (
            pos_1.start()
            < pos_2.start()
            < pos_3.start()
            < pos_10.start()
            < pos_20.start()
        ), (
            "Numbers not in numeric order: "
            f"1@{pos_1.start()}, 2@{pos_2.start()}, 3@{pos_3.start()}, "
            f"10@{pos_10.start()}, 20@{pos_20.start()}"
        )


class TestIssuePaginationClamping:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        project = Project(
            name="snapcraft", category="application", github_org="canonical"
        )
        test_db_session.add(project)
        await test_db_session.flush()
        test_db_session.add(
            Issue(
                project_id=project.id,
                source="github",
                external_id="1",
                issue_type="issue",
                title="Only issue",
                state="open",
                labels=[],
                last_fetched_at=datetime.now(tz=UTC),
            )
        )
        await test_db_session.commit()

    def test_page_beyond_total_clamps(
        self, test_client: TestClient, seeded: None
    ) -> None:
        """Requesting a page beyond total should show the last page, not empty."""
        response = test_client.get("/issues", params={"page": 999})
        assert response.status_code == 200
        assert "Only issue" in response.text


class TestPaginationPreservesSourceFilter:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        project = Project(
            name="snapcraft", category="application", github_org="canonical"
        )
        test_db_session.add(project)
        await test_db_session.flush()
        for i in range(60):
            test_db_session.add(
                Issue(
                    project_id=project.id,
                    source="github",
                    external_id=str(i + 1),
                    issue_type="issue",
                    title=f"Issue {i + 1}",
                    state="open",
                    labels=[],
                    last_fetched_at=datetime.now(tz=UTC),
                )
            )
        await test_db_session.commit()

    def test_pagination_includes_source_in_url(
        self, test_client: TestClient, seeded: None
    ) -> None:
        """Pagination links must preserve the source filter."""
        response = test_client.get("/issues", params={"source": "github"})
        assert response.status_code == 200
        assert "source=github" in response.text


class TestIssueStateFilter:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        project = Project(
            name="snapcraft", category="application", github_org="canonical"
        )
        test_db_session.add(project)
        await test_db_session.flush()
        test_db_session.add_all(
            [
                Issue(
                    project_id=project.id,
                    source="github",
                    external_id="1",
                    issue_type="issue",
                    title="Open issue",
                    state="open",
                    labels=[],
                    last_fetched_at=datetime.now(tz=UTC),
                ),
                Issue(
                    project_id=project.id,
                    source="github",
                    external_id="2",
                    issue_type="issue",
                    title="Closed issue",
                    state="closed",
                    labels=[],
                    last_fetched_at=datetime.now(tz=UTC),
                ),
            ]
        )
        await test_db_session.commit()

    def test_default_shows_open_only(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/issues")
        assert "Open issue" in response.text
        assert "Closed issue" not in response.text

    def test_filter_closed(self, test_client: TestClient, seeded: None) -> None:
        response = test_client.get("/issues", params={"state": "closed"})
        assert "Closed issue" in response.text
        assert "Open issue" not in response.text

    def test_filter_all_states(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/issues", params={"state": "open,closed"})
        assert "Open issue" in response.text
        assert "Closed issue" in response.text
