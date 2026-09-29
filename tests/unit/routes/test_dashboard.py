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

from tests.helpers.html import normalize_text, parse_html

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
        assert "Open PR age &amp; response" in response.text
        assert "Resolution throughput" in response.text
        assert "Untriaged backlog" in response.text
        assert "All-time volume" in response.text
        assert "Apps with least-recent releases" in response.text
        assert "Aging contributor PRs" in response.text
        assert "Need triage" in response.text
        assert "Quick wins" in response.text
        assert "Project health &amp; navigation" in response.text


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


class TestDashboardSemanticStructure:
    """Structural assertions on the rendered dashboard markup."""

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

    def _render(self, session) -> str:
        app = create_app()
        app.router.lifespan_context = _noop_lifespan

        async def _override():
            yield session

        app.dependency_overrides[get_db_session] = _override

        with TestClient(app) as client:
            response = client.get("/")

        assert response.status_code == 200
        return response.text

    def test_kpi_cards_and_spotlights_present(self) -> None:
        """Each KPI card and spotlight renders with a stable hook."""
        doc = parse_html(self._render(_DashboardSession()))

        for selector in (
            "#kpi-velocity",
            "#kpi-throughput",
            "#kpi-untriaged",
            "#kpi-volume",
            "#spotlight-least-recent-releases",
            "#spotlight-aging-prs",
            "#spotlight-needs-triage",
            "#spotlight-quick-wins",
            "[data-testid='project-health-applications']",
            "[data-testid='project-health-libraries']",
            "[data-testid='project-health-other']",
        ):
            assert doc.exists(selector), selector

    def test_empty_dashboard_renders_placeholders(self) -> None:
        """With no data the KPI values fall back to precomputed placeholders."""
        doc = parse_html(self._render(_DashboardSession()))

        assert doc.text("[data-testid='velocity-contributor-age']") == "—"
        assert doc.text("[data-testid='throughput-total-30d']") == "0"
        assert doc.text("[data-testid='untriaged-issues-new']") == "+0"
        assert doc.attr("[data-testid='untriaged-issues-new']", "class") == ""
        assert doc.text("[data-testid='volume-total-net']") == "0 in 30d"
        assert doc.count("[data-testid='aging-pr-row']") == 0

    def test_volume_net_tooltip_is_precomputed(self) -> None:
        """The net-change tooltip is rendered by Python, not assembled in Jinja."""
        doc = parse_html(self._render(_DashboardSession()))

        tooltip = doc.attr("[data-testid='volume-issues-net']", "title")
        assert tooltip == (
            "Net change in open issues over the last 30 days "
            "(0 opened \u2212 0 closed = 0 net change)"
        )
        assert doc.attr("[data-testid='volume-issues-net']", "data-tooltip") == tooltip

    def test_project_health_row_labels_and_badges(
        self, test_db_session, seeded
    ) -> None:
        """A seeded project renders labels and badge classes from the view model."""
        doc = parse_html(self._render(test_db_session))
        row = doc.scope(
            "[data-testid='project-health-applications'] "
            "[data-testid='project-health-row'][data-project='snapcraft']"
        )

        assert "2 issues" in row.text("a.nav-chip")
        pill = row.require("[data-testid='project-triage-pill']")
        assert "dash-pill--green" in " ".join(pill["class"])
        assert normalize_text(pill.get_text(" ")) == "3 untriaged (100.0%)"

    def test_project_health_links_to_github(self, test_db_session, seeded) -> None:
        """The GitHub link is built in Python from the project's org."""
        doc = parse_html(self._render(test_db_session))
        row = doc.scope("[data-testid='project-health-row'][data-project='snapcraft']")

        assert "https://github.com/canonical/snapcraft" in row.links()
