"""Tests for the dashboard routes."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

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


class _EmptyScalars:
    def all(self):
        return []


class _EmptyResult:
    def scalars(self):
        return _EmptyScalars()

    def scalar(self):
        return None

    def all(self):
        return []

    def one(self):
        m = MagicMock()
        m.total = 0
        m.new_30d = 0
        return m

    def __iter__(self):
        return iter(())


class _DashboardSession:
    """Mock session for dashboard tests."""

    def __init__(self, counts=None):
        self.counts = counts or [0, 0, 0]
        self.count_idx = 0

    async def scalar(self, _query):
        if self.count_idx < len(self.counts):
            val = self.counts[self.count_idx]
            self.count_idx += 1
            return val
        return 0

    async def execute(self, _query):
        return _EmptyResult()


class TestDashboardIndex:
    """Tests for the dashboard index route."""

    def test_index_returns_html(self) -> None:
        """GET / returns HTML with dashboard content."""
        app = create_app()
        app.router.lifespan_context = _noop_lifespan

        async def fake_session():
            yield _DashboardSession()

        app.dependency_overrides[get_db_session] = fake_session

        with TestClient(app) as client:
            response = client.get("/")

            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
            assert "<h2>Dashboard</h2>" in response.text

    def test_index_includes_landmarks_and_stat_cards(self) -> None:
        """GET / includes page landmarks and operational KPI cards."""
        app = create_app()
        app.router.lifespan_context = _noop_lifespan

        async def fake_session():
            yield _DashboardSession(counts=[3, 7, 4])

        app.dependency_overrides[get_db_session] = fake_session

        with TestClient(app) as client:
            response = client.get("/")

        assert response.status_code == 200
        assert 'role="banner"' in response.text
        assert 'aria-label="Mobile menu"' in response.text
        assert 'role="main"' in response.text
        assert 'role="contentinfo"' in response.text
        assert "Open PR Age &amp; Response" in response.text
        assert "Resolution Throughput" in response.text
        assert "Untriaged Backlog" in response.text
        assert "All-Time Volume" in response.text
        assert "Apps with Least-Recent Releases" in response.text
        assert "Aging Contributor PRs" in response.text
        assert "Needs Triage" in response.text
        assert "Quick-Wins Radar" in response.text
        assert "Project Health &amp; Navigation" in response.text


class TestDashboardIndexWithData:
    """DB-backed tests for the dashboard index route."""

    @pytest.fixture
    async def seeded(self, test_db_session) -> None:
        p = Project(name="snapcraft", category="application", github_org="canonical")
        test_db_session.add(p)
        await test_db_session.flush()

        for i, (itype, state) in enumerate(
            [
                ("issue", "open"),
                ("issue", "open"),
                ("pull_request", "open"),
                ("issue", "closed"),
            ]
        ):
            test_db_session.add(
                Issue(
                    project_id=p.id,
                    source="github",
                    external_id=str(i),
                    issue_type=itype,
                    title=f"test {i}",
                    state=state,
                    labels=[],
                    last_fetched_at=datetime.now(tz=UTC),
                )
            )

        await test_db_session.commit()

    def test_dashboard_shows_project(self, test_db_session, seeded) -> None:
        app = create_app()
        app.router.lifespan_context = _noop_lifespan

        async def _override():
            yield test_db_session

        app.dependency_overrides[get_db_session] = _override

        with TestClient(app) as client:
            response = client.get("/")

        assert response.status_code == 200
        assert "snapcraft" in response.text
