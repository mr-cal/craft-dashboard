"""Integration tests for dashboard and issues routes with real DB data."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.config import DashboardConfig
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.issue_link import IssueLink
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import make_evaluation, make_issue, make_project
from tests.helpers.html import parse_html

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


async def _seed_entities(test_db_session: AsyncSession, *entities: object) -> None:
    test_db_session.add_all(list(entities))
    await test_db_session.commit()


class TestDashboardWithData:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        await _seed_project_with_issues(test_db_session)

    def test_dashboard_shows_project_name(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/")

        assert response.status_code == 200
        doc = parse_html(response)
        assert doc.has_text("snapcraft", ".project-health-table")

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
        doc = parse_html(response)
        assert doc.has_text("first dashboard issue", "#issue-table")

    def test_issues_table_partial(self, test_client: TestClient, seeded: None) -> None:
        response = test_client.get("/issues/table")

        assert response.status_code == 200
        doc = parse_html(response)
        assert doc.has_text("first dashboard issue", "#issue-table")

    def test_issues_filter_by_project(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/issues", params={"project": "snapcraft"})

        assert response.status_code == 200
        doc = parse_html(response)
        assert doc.has_text("first dashboard issue", "#issue-table")

    def test_issues_filter_nonexistent(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/issues", params={"project": "nonexistent"})

        assert response.status_code == 200
        doc = parse_html(response)
        assert not doc.has_text("first dashboard issue", "#issue-table")
        assert doc.has_text(
            "No issues found matching the current filters.", "#issue-table"
        )

    def test_issues_table_with_comma_separated_projects(
        self, test_client: TestClient, seeded: None
    ) -> None:
        """Comma-separated project values (from multiselect) should not 422."""
        response = test_client.get(
            "/issues/table", params={"project": "snapcraft,charmcraft"}
        )

        assert response.status_code == 200

    def test_issues_table_with_all_filter_params(
        self, test_client: TestClient, seeded: None
    ) -> None:
        """All filter params together should not 422."""
        response = test_client.get(
            "/issues/table",
            params={
                "project": "snapcraft",
                "source": "",
                "state": "open",
                "type": "",
                "action": "",
                "author_role": "",
                "sort": "impact",
                "search": "",
                "per_page": "100",
                "scores": "impact,actionability",
                "llm_status": "",
            },
        )

        assert response.status_code == 200

    def test_issues_empty_db(self, test_client: TestClient) -> None:
        response = test_client.get("/issues")

        assert response.status_code == 200


class TestIssuesPageMarkup:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        await _seed_project_with_issues(test_db_session)

    def test_all_htmx_get_elements_have_loading_indicator(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/issues")

        assert response.status_code == 200
        doc = parse_html(response)
        htmx_elements = doc.select("[hx-get]")

        assert htmx_elements
        assert all(
            element.get("hx-indicator") == "#loading-indicator"
            for element in htmx_elements
        )

    def test_multiselect_markup_has_accessibility_attributes(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/issues")

        assert response.status_code == 200
        doc = parse_html(response)
        input_wraps = doc.select("div.multiselect__input-wrap")
        option_lists = doc.select("div.multiselect__options")
        options = doc.select("label.multiselect__option")

        assert len(input_wraps) == 6
        assert all(element.get("role") == "combobox" for element in input_wraps)
        assert all(element.has_attr("aria-expanded") for element in input_wraps)
        assert all(element.get("aria-haspopup") == "listbox" for element in input_wraps)
        assert {element.get("aria-label") for element in input_wraps} == {
            "Select projects",
            "Select author roles",
            "Select states",
            "Select actions",
            "Select types",
            "Select visible columns",
        }
        assert len(option_lists) == 6
        assert all(element.get("role") == "listbox" for element in option_lists)
        assert options
        assert all(element.get("role") == "option" for element in options)

    def test_active_sort_header_is_marked_active(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/issues", params={"sort": "age"})

        assert response.status_code == 200
        doc = parse_html(response)
        age_link = doc.require('th[data-col="age"] a[hx-get]')

        assert "is-active" in age_link.get("class", [])

    def test_base_template_includes_htmx_error_feedback(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/issues")

        assert response.status_code == 200
        doc = parse_html(response)
        scripts = "\n".join(script.get_text() for script in doc.select("script"))
        assert doc.exists("#toast-container")
        assert "showToast(message, type)" in scripts
        assert 'document.body.addEventListener("htmx:responseError"' in scripts
        assert 'document.body.addEventListener("htmx:sendError"' in scripts

    def test_issues_page_has_single_state_hidden_input(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/issues")

        assert response.status_code == 200
        doc = parse_html(response)
        assert doc.count('input[type="hidden"][name="state"]') == 1


class TestDashboardExcludesAggregate:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        test_db_session.add_all(
            [
                Project(
                    name="snapcraft", category="application", github_org="canonical"
                ),
                Project(name="craft-parts", category="library", github_org="canonical"),
                Project(
                    name="all-projects",
                    category="aggregate",
                    github_org="canonical",
                    display_order=-1,
                ),
            ]
        )
        await test_db_session.commit()

    def test_project_count_excludes_aggregate(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/")
        assert response.status_code == 200
        doc = parse_html(response)
        assert not doc.has_text("all-projects", ".project-health-table")

    def test_aggregate_not_in_tables(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/")
        assert response.status_code == 200
        doc = parse_html(response)
        assert not doc.has_text("all-projects", ".project-health-table")


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
        doc = parse_html(response)
        assert doc.texts('#issue-table tbody td[data-col="issue"]') == [
            "snapcraft issue #1",
            "snapcraft issue #2",
            "snapcraft issue #3",
            "snapcraft issue #10",
            "snapcraft issue #20",
        ]


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
        doc = parse_html(response)
        assert doc.has_text("Only issue", "#issue-table")


class TestPaginationPreservesSourceFilter:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        project = Project(
            name="snapcraft", category="application", github_org="canonical"
        )
        test_db_session.add(project)
        await test_db_session.flush()
        for i in range(110):
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
        doc = parse_html(response)
        assert any("source=github" in link for link in doc.links())


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
        doc = parse_html(response)
        assert doc.has_text("Open issue", "#issue-table")
        assert not doc.has_text("Closed issue", "#issue-table")

    def test_filter_closed(self, test_client: TestClient, seeded: None) -> None:
        response = test_client.get("/issues", params={"state": "closed"})
        doc = parse_html(response)
        assert doc.has_text("Closed issue", "#issue-table")
        assert not doc.has_text("Open issue", "#issue-table")

    def test_filter_all_states(self, test_client: TestClient, seeded: None) -> None:
        response = test_client.get("/issues", params={"state": "open,closed"})
        doc = parse_html(response)
        assert doc.has_text("Open issue", "#issue-table")
        assert doc.has_text("Closed issue", "#issue-table")


class TestIssueDetailRelatedWork:
    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        project = make_project(id=1, name="rockcraft")
        from_issue = make_issue(
            id=1,
            project_id=1,
            external_id="1",
            title="Broken pack after build refresh",
        )
        to_issue = make_issue(
            id=2,
            project_id=1,
            external_id="2",
            title="Fix build refresh regression",
        )
        evaluation = make_evaluation(id=1, issue_id=1, latest=True)
        link = IssueLink(
            from_issue_id=1,
            llm_evaluation_id=1,
            to_issue_id=2,
            to_ref="rockcraft#2",
            kind="likely_fixed_by",
            confidence=80,
            note="Fixed in the branch refresh change.",
            source="evaluator",
        )
        await _seed_entities(
            test_db_session,
            project,
            from_issue,
            to_issue,
            evaluation,
            link,
        )

    def test_issue_detail_related_work_panel_renders_linked_issue(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/issues/rockcraft/1")

        assert response.status_code == 200
        doc = parse_html(response)
        related_work = next(
            card
            for card in doc.select(".issue-detail-card")
            if card.select_one("h3") and "Related work" in card.get_text(" ")
        )
        related_work_text = related_work.get_text(" ", strip=True)
        assert "Likely Fixed By" in related_work_text
        assert related_work.select_one('a[href="/issues/rockcraft/2"]') is not None
        assert "rockcraft#2" in related_work_text
        assert "confidence 80%" in related_work_text
        assert "Fixed in the branch refresh change." in related_work_text

    def test_issue_table_related_work_indicator_is_shown_for_linked_rows(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/issues/table", params={"project": "rockcraft"})

        assert response.status_code == 200
        doc = parse_html(response)
        assert doc.exists(".related-work-indicator")


class TestIssueDetailExcludesClaimBookkeepingRows:
    """`pending`/`released:*` rows are worker claim bookkeeping, not real
    evaluations, and must never appear in the evaluation history or be
    picked as the "current evaluation" for an issue."""

    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(
            id=1,
            project_id=1,
            external_id="6413",
            title="Flaky build on core24",
        )
        real_evaluation = make_evaluation(
            id=1,
            issue_id=1,
            model_name="gpt-4.1",
            summary="A real evaluation summary.",
            suggested_action="needs_review",
            evaluated_at=datetime(2026, 9, 3, 10, 0, tzinfo=UTC),
            latest=False,
        )
        # Newer than the real evaluation, simulating claim/release churn
        # that happened after the real evaluation was recorded.
        released_row = make_evaluation(
            id=2,
            issue_id=1,
            model_name="released:related_endpoint_unreachable",
            summary=None,
            suggested_action=None,
            evaluated_at=datetime(2026, 9, 3, 11, 0, tzinfo=UTC),
            latest=False,
        )
        pending_row = make_evaluation(
            id=3,
            issue_id=1,
            model_name="pending",
            summary=None,
            suggested_action=None,
            evaluated_at=datetime(2026, 9, 3, 11, 2, tzinfo=UTC),
            latest=True,
        )
        await _seed_entities(
            test_db_session,
            project,
            issue,
            real_evaluation,
            released_row,
            pending_row,
        )

    def test_placeholder_rows_are_excluded_from_history_and_current_evaluation(
        self, test_client: TestClient, seeded: None
    ) -> None:
        response = test_client.get("/issues/snapcraft/6413")

        assert response.status_code == 200
        doc = parse_html(response)
        assert doc.has_text("A real evaluation summary.")
        assert not doc.has_text("pending")
        assert not doc.has_text("released:related_endpoint_unreachable")
