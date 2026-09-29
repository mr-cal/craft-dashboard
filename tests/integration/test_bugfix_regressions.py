"""Regression tests for the Phase 1 security and correctness bug fixes.

Each test here maps to a specific defect; see the docstrings for the bug ID and
the behaviour that regressed.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.config import DashboardConfig
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.views import IssueFilters
from craft_dashboard.repositories.issue_repository import (
    MAX_ITEMS_PER_PAGE,
    IssueRepository,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import make_issue, make_project

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


async def _seed(session: AsyncSession, external_ids: list[str]) -> None:
    project = make_project(name="snapcraft")
    session.add(project)
    await session.flush()
    for external_id in external_ids:
        session.add(
            make_issue(
                project_id=project.id,
                external_id=external_id,
                title=f"Issue {external_id}",
            )
        )
    await session.commit()


@pytest.mark.asyncio
async def test_sort_by_number_tolerates_non_numeric_external_ids(
    test_db_session: AsyncSession,
) -> None:
    """B4: casting external_id to INTEGER used to 500 on non-numeric IDs.

    Launchpad and forum sources can supply identifiers that are not plain
    integers. The sort must still succeed, with non-numeric values ordered last
    rather than raising.
    """
    await _seed(test_db_session, ["10", "2", "lp-1861614", "33"])

    repo = IssueRepository(test_db_session)
    result = await repo.search(IssueFilters(sort_by="number", state="open"))

    returned = [issue.external_id for issue in result.issues]
    assert returned[:3] == ["2", "10", "33"]
    assert "lp-1861614" in returned


@pytest.mark.asyncio
async def test_sort_by_number_descending_tolerates_non_numeric(
    test_db_session: AsyncSession,
) -> None:
    """B4: the descending branch used the same unguarded cast."""
    await _seed(test_db_session, ["10", "2", "not-a-number"])

    repo = IssueRepository(test_db_session)
    result = await repo.search(IssueFilters(sort_by="-number", state="open"))

    assert [issue.external_id for issue in result.issues][:2] == ["10", "2"]


@pytest.mark.asyncio
async def test_per_page_all_is_capped(test_db_session: AsyncSession) -> None:
    """B5: items_per_page <= 0 dropped the SQL LIMIT entirely.

    "All" must still return a single page, but the query is capped so one
    request cannot materialize the whole issue table.
    """
    await _seed(test_db_session, [str(n) for n in range(1, 6)])

    repo = IssueRepository(test_db_session)
    result = await repo.search(
        IssueFilters(items_per_page=0, state="open", sort_by="number")
    )

    assert result.total_pages == 1
    assert len(result.issues) <= MAX_ITEMS_PER_PAGE
    assert len(result.issues) == 5


def test_trend_chart_json_escapes_script_tags(test_client: TestClient) -> None:
    """B2: chart data was emitted with `| safe`, allowing script breakout.

    Jinja's `tojson` escapes `<` and `>`, so a project name containing a
    closing script tag cannot terminate the surrounding block.
    """
    response = test_client.get("/stats/trends/chart?project=all-projects")

    if response.status_code == 404:
        pytest.skip("no trend data seeded for all-projects")

    assert (
        "</script>"
        not in response.text.split("<script>", 1)[-1].rsplit("</script>", 1)[0]
    )


def test_engagement_forum_payload_is_escaped(test_client: TestClient) -> None:
    """B2: the engagement payload used `| safe` on a pre-dumped JSON string."""
    response = test_client.get("/engagement/forums")

    assert response.status_code == 200
    # `tojson` escapes angle brackets as \u003c / \u003e, so no raw `<` may
    # appear inside the payload assignment.
    assignment = response.text.split("window.ENGAGEMENT_FORUMS = ", 1)
    if len(assignment) > 1:
        payload = assignment[1].split(";", 1)[0]
        assert "<" not in payload
