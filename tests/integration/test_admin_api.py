"""Integration tests for admin API endpoints with real DB."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.config import DashboardConfig
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.collection_run import CollectionRun
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.issue_activity import IssueActivity
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.routes import admin as admin_routes
from craft_dashboard.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

_TEST_ADMIN_TOKEN = "test-admin-token"


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


@pytest.fixture
def app_with_db(test_db_session: AsyncSession) -> tuple[FastAPI, str]:
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.state.config = DashboardConfig()
    app.state.settings = Settings()
    app.state.settings.admin_token = _TEST_ADMIN_TOKEN
    token = _TEST_ADMIN_TOKEN

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield test_db_session

    app.dependency_overrides[get_db_session] = _override
    return app, token


@pytest.fixture
def client(app_with_db: tuple[FastAPI, str]) -> TestClient:
    app, _ = app_with_db
    with TestClient(app) as test_client:
        yield test_client


def _stub_admin_page_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_api_budget(self):
        return {
            "core_remaining": 5000,
            "core_limit": 5000,
            "core_reset": datetime(2025, 1, 10, 13, 0, tzinfo=UTC),
            "graphql_remaining": 5000,
            "graphql_limit": 5000,
            "graphql_reset": datetime(2025, 1, 10, 13, 0, tzinfo=UTC),
        }

    monkeypatch.setattr(
        admin_routes.AdminService,
        "get_api_budget",
        _fake_api_budget,
    )


class TestAdminPageIntegration:
    """Integration tests for the admin dashboard page."""

    def test_admin_page_renders(
        self,
        client: TestClient,
        test_db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The admin page renders HTML when real DB data exists."""
        _stub_admin_page_metrics(monkeypatch)

        async def _seed() -> None:
            test_db_session.add(
                Project(
                    name="admin-project", category="application", github_org="canonical"
                )
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = client.get("/admin")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "<h2>Admin</h2>" in response.text

    def test_admin_page_empty_db(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The admin page still renders when the database is empty."""
        _stub_admin_page_metrics(monkeypatch)
        response = client.get("/admin")

        assert response.status_code == 200
        assert "Project full-refresh schedule" in response.text

    def test_admin_page_shows_llm_evaluation_service_section(
        self,
        client: TestClient,
        test_db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The read-only LLM service section shows status and recent evals."""
        _stub_admin_page_metrics(monkeypatch)

        async def _seed() -> None:
            project = Project(
                name="snapcraft", category="application", github_org="canonical"
            )
            test_db_session.add(project)
            await test_db_session.flush()
            issue = Issue(
                project_id=project.id,
                source="github",
                external_id="42",
                issue_type="issue",
                title="Build fails on arm64",
                body="Body",
                state="open",
                author="dev",
                author_is_maintainer=False,
                author_is_bot=False,
                labels=[],
                created_at=datetime(2025, 1, 1, tzinfo=UTC),
                updated_at=datetime(2025, 1, 1, tzinfo=UTC),
                url="https://example.com/snapcraft/issues/42",
                metadata_={},
                comments=[],
                last_fetched_at=datetime(2025, 1, 1, tzinfo=UTC),
            )
            test_db_session.add(issue)
            await test_db_session.flush()
            test_db_session.add(
                LLMEvaluation(
                    issue_id=issue.id,
                    model_name="gpt-4.1",
                    summary="Summary",
                    suggested_action="keep_open",
                    suggested_action_reason="Reason",
                    scores={},
                    evaluated_at=datetime(2025, 1, 5, tzinfo=UTC),
                    issue_data_hash="hash",
                    latest=True,
                )
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = client.get("/admin")

        assert response.status_code == 200
        assert "LLM evaluation service" in response.text
        assert "gpt-4.1" in response.text
        assert "keep_open" in response.text
        assert "Build fails on arm64" in response.text
        # Cost figures are deliberately not surfaced here.
        assert "openrouter cost" not in response.text.lower()


class TestAdminHealthIntegration:
    """Integration tests for the admin health endpoint."""

    def test_health_ok(
        self, client: TestClient, app_with_db: tuple[FastAPI, str]
    ) -> None:
        """Health returns ok with an empty but reachable DB."""
        _, token = app_with_db

        response = client.get(
            "/admin/health", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "database": "ok",
            "failing_collectors": [],
        }

    def test_health_requires_auth(self, client: TestClient) -> None:
        """Health requires a valid bearer token."""
        response = client.get("/admin/health")

        assert response.status_code == 401


class TestAdminAuthIntegration:
    """Integration tests for admin cookie authentication."""

    def test_admin_auth_sets_cookie_and_allows_cookie_auth(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
    ) -> None:
        """Valid admin auth sets a secure session cookie usable by admin routes."""
        _, token = app_with_db

        response = client.post(
            "/admin/auth",
            json={"token": token},
            headers={"Origin": "http://localhost", "Host": "localhost"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "authenticated"
        assert client.cookies.get("admin_session") == token
        set_cookie = response.headers["set-cookie"]
        assert "HttpOnly" in set_cookie
        assert "SameSite=strict" in set_cookie

        refresh_response = client.post(
            "/admin/refresh",
            headers={"Origin": "http://localhost", "Host": "localhost"},
        )

        assert refresh_response.status_code == 202
        assert refresh_response.json()["status"] == "refresh_queued"

    def test_admin_auth_rejects_invalid_token(self, client: TestClient) -> None:
        """Invalid admin auth requests are rejected."""
        response = client.post(
            "/admin/auth",
            json={"token": "wrong-token"},
            headers={"Origin": "http://localhost", "Host": "localhost"},
        )

        assert response.status_code == 401
        assert client.cookies.get("admin_session") is None

    def test_admin_logout_clears_cookie(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
    ) -> None:
        """Logout clears the admin session cookie."""
        _, token = app_with_db

        auth_response = client.post(
            "/admin/auth",
            json={"token": token},
            headers={"Origin": "http://localhost", "Host": "localhost"},
        )
        assert auth_response.status_code == 200

        response = client.post(
            "/admin/logout",
            headers={"Origin": "http://localhost", "Host": "localhost"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "logged_out"
        assert client.cookies.get("admin_session") is None
        assert "admin_session=" in response.headers["set-cookie"]

        refresh_response = client.post(
            "/admin/refresh",
            headers={"Origin": "http://localhost", "Host": "localhost"},
        )
        assert refresh_response.status_code == 401


class TestAdminRefreshIntegration:
    """Integration tests for refresh endpoints."""

    @pytest.mark.parametrize(
        ("origin", "host", "expected_status"),
        [
            ("http://localhost", "localhost", 202),
            ("http://evil.com", "localhost", 403),
            ("http://evil.com?localhost", "localhost", 403),
        ],
    )
    def test_refresh_origin_validation(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
        origin: str,
        host: str,
        expected_status: int,
    ) -> None:
        """Refresh compares the parsed Origin netloc with Host exactly."""
        _, token = app_with_db

        response = client.post(
            "/admin/refresh",
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": origin,
                "Host": host,
            },
        )

        assert response.status_code == expected_status

    def test_re_evaluate_route_is_removed(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
    ) -> None:
        """Re-evaluate is no longer available."""
        _app, token = app_with_db

        response = client.post(
            "/admin/re-evaluate", headers={"Authorization": "******"}
        )

        assert response.status_code == 404


class TestAdminLogsIntegration:
    """Integration tests for the admin logs endpoint."""

    def test_logs_with_valid_token(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
    ) -> None:
        """Logs returns plain text when called with a valid bearer token."""
        _, token = app_with_db

        response = client.get(
            "/admin/logs", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]

    def test_logs_returns_content(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
    ) -> None:
        """Logs always returns some body text, even without journalctl output."""
        _, token = app_with_db

        response = client.get(
            "/admin/logs", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert response.text.strip() != ""

    def test_logs_escape_html_entities(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Logs escape HTML entities before returning output."""
        _, token = app_with_db

        class _FakeProcess:
            async def communicate(self) -> tuple[bytes, bytes]:
                return (b"<script>alert(1)</script>", b"")

        async def _fake_create_subprocess_exec(*args, **kwargs):
            return _FakeProcess()

        monkeypatch.setattr(
            admin_routes.asyncio,
            "create_subprocess_exec",
            _fake_create_subprocess_exec,
        )

        response = client.get(
            "/admin/logs", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert response.text == "&lt;script&gt;alert(1)&lt;/script&gt;"

    def test_logs_use_async_subprocess_exec(
        self,
        client: TestClient,
        app_with_db: tuple[FastAPI, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Logs use asyncio.create_subprocess_exec for journalctl."""
        _, token = app_with_db
        seen: dict[str, tuple | dict] = {}

        class _FakeProcess:
            async def communicate(self) -> tuple[bytes, bytes]:
                return (b"async logs", b"")

        async def _fake_create_subprocess_exec(*args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs
            return _FakeProcess()

        monkeypatch.setattr(
            admin_routes.asyncio,
            "create_subprocess_exec",
            _fake_create_subprocess_exec,
        )

        response = client.get(
            "/admin/logs", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert response.text == "async logs"
        assert seen["args"] == (
            "journalctl",
            "-u",
            "collect-data",
            "-u",
            "craft-dashboard",
            "-u",
            "run-llm",
            "-n",
            "100",
            "--no-pager",
            "--output",
            "short",
        )
        assert seen["kwargs"] == {
            "stdout": admin_routes.asyncio.subprocess.PIPE,
            "stderr": admin_routes.asyncio.subprocess.PIPE,
        }


class TestCollectionRunIssuesEndpoint:
    """Integration tests for the collection run issues expansion endpoint."""

    def test_returns_issues_for_run(
        self,
        client: TestClient,
        test_db_session: AsyncSession,
    ) -> None:
        """GET /admin/collection-runs/{id}/issues returns an HTML fragment with issues."""
        now = datetime(2025, 6, 21, 12, 0, tzinfo=UTC)

        async def _seed() -> int:
            project = Project(
                name="snapcraft", category="application", github_org="canonical"
            )
            test_db_session.add(project)
            await test_db_session.flush()

            run = CollectionRun(
                source="github",
                started_at=now,
                status="completed",
                projects_processed=1,
                issues_collected=2,
                errors=[],
            )
            test_db_session.add(run)
            await test_db_session.flush()

            test_db_session.add_all(
                [
                    Issue(
                        project_id=project.id,
                        collection_run_id=run.id,
                        source="github",
                        external_id="1",
                        issue_type="issue",
                        title="Alpha issue",
                        state="open",
                        author="dev",
                        author_is_maintainer=False,
                        author_is_bot=False,
                        labels=[],
                        url="https://github.com/canonical/snapcraft/issues/1",
                        metadata_={},
                        comments=[],
                        last_fetched_at=now,
                    ),
                    Issue(
                        project_id=project.id,
                        collection_run_id=run.id,
                        source="github",
                        external_id="2",
                        issue_type="issue",
                        title="Beta issue",
                        state="open",
                        author="dev",
                        author_is_maintainer=False,
                        author_is_bot=False,
                        labels=[],
                        url="https://github.com/canonical/snapcraft/issues/2",
                        metadata_={},
                        comments=[],
                        last_fetched_at=now,
                    ),
                ]
            )
            await test_db_session.commit()
            return run.id

        run_id = asyncio.get_event_loop().run_until_complete(_seed())
        response = client.get(f"/admin/collection-runs/{run_id}/issues")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Alpha issue" in response.text
        assert "Beta issue" in response.text
        assert "snapcraft" in response.text

    def test_returns_empty_message_when_no_issues(
        self,
        client: TestClient,
        test_db_session: AsyncSession,
    ) -> None:
        """Returns 'No issues collected' message for a run with no issues."""
        now = datetime(2025, 6, 21, 12, 0, tzinfo=UTC)

        async def _seed() -> int:
            run = CollectionRun(
                source="github",
                started_at=now,
                status="completed",
                projects_processed=0,
                issues_collected=0,
                errors=[],
            )
            test_db_session.add(run)
            await test_db_session.flush()
            await test_db_session.commit()
            return run.id

        run_id = asyncio.get_event_loop().run_until_complete(_seed())
        response = client.get(f"/admin/collection-runs/{run_id}/issues")

        assert response.status_code == 200
        assert "No issues collected" in response.text

    def test_shows_truncation_message_when_over_limit(
        self,
        client: TestClient,
        test_db_session: AsyncSession,
    ) -> None:
        """Shows 'Showing N of M' when run has more than 100 issues."""
        now = datetime(2025, 6, 21, 12, 0, tzinfo=UTC)

        async def _seed() -> int:
            project = Project(
                name="rockcraft", category="application", github_org="canonical"
            )
            test_db_session.add(project)
            await test_db_session.flush()

            run = CollectionRun(
                source="github",
                started_at=now,
                status="completed",
                projects_processed=1,
                issues_collected=105,
                errors=[],
            )
            test_db_session.add(run)
            await test_db_session.flush()

            issues = [
                Issue(
                    project_id=project.id,
                    collection_run_id=run.id,
                    source="github",
                    external_id=str(i),
                    issue_type="issue",
                    title=f"Issue {i}",
                    state="open",
                    author="dev",
                    author_is_maintainer=False,
                    author_is_bot=False,
                    labels=[],
                    metadata_={},
                    comments=[],
                    last_fetched_at=now,
                )
                for i in range(105)
            ]
            test_db_session.add_all(issues)
            await test_db_session.commit()
            return run.id

        run_id = asyncio.get_event_loop().run_until_complete(_seed())
        response = client.get(f"/admin/collection-runs/{run_id}/issues")

        assert response.status_code == 200
        assert "Showing 100 of 105" in response.text


class TestRecentActivityFragmentEndpoint:
    """Integration tests for the paginated recent activity fragment."""

    def test_returns_first_page(
        self,
        client: TestClient,
        test_db_session: AsyncSession,
    ) -> None:
        """GET /admin/recent-activity returns 10 rows and a disabled Newer button."""
        now = datetime(2025, 6, 21, 12, 0, tzinfo=UTC)

        async def _seed() -> None:
            project = Project(
                name="snapcraft", category="application", github_org="canonical"
            )
            test_db_session.add(project)
            await test_db_session.flush()

            test_db_session.add_all(
                [
                    IssueActivity(
                        project_id=project.id,
                        issue_number=i,
                        change_type="updated",
                        title=f"Change {i}",
                        occurred_at=now - timedelta(hours=i),
                    )
                    for i in range(15)
                ]
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = client.get("/admin/recent-activity")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert response.text.count("Change ") == 10
        assert "Change 0" in response.text
        assert "Change 9" in response.text
        assert "Change 10" not in response.text
        assert 'hx-get="/admin/recent-activity?offset=10' in response.text

    def test_returns_second_page(
        self,
        client: TestClient,
        test_db_session: AsyncSession,
    ) -> None:
        """Requesting offset=10 returns the remaining rows with Older disabled."""
        now = datetime(2025, 6, 21, 12, 0, tzinfo=UTC)

        async def _seed() -> None:
            project = Project(
                name="snapcraft", category="application", github_org="canonical"
            )
            test_db_session.add(project)
            await test_db_session.flush()

            test_db_session.add_all(
                [
                    IssueActivity(
                        project_id=project.id,
                        issue_number=i,
                        change_type="updated",
                        title=f"Change {i}",
                        occurred_at=now - timedelta(hours=i),
                    )
                    for i in range(15)
                ]
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = client.get("/admin/recent-activity", params={"offset": 10})

        assert response.status_code == 200
        assert response.text.count("Change ") == 5
        assert "Change 10" in response.text
        assert "Change 14" in response.text
        assert 'hx-get="/admin/recent-activity?offset=0' in response.text


class TestRecentEvaluationsFragmentEndpoint:
    """Integration tests for the paginated recent evaluations fragment."""

    def test_returns_first_page(
        self,
        client: TestClient,
        test_db_session: AsyncSession,
    ) -> None:
        """GET /admin/recent-evaluations returns 10 rows and a disabled Newer button."""
        now = datetime(2025, 6, 21, 12, 0, tzinfo=UTC)

        async def _seed() -> None:
            project = Project(
                name="snapcraft", category="application", github_org="canonical"
            )
            test_db_session.add(project)
            await test_db_session.flush()

            issues = [
                Issue(
                    project_id=project.id,
                    source="github",
                    external_id=str(i),
                    issue_type="issue",
                    title=f"Issue {i}",
                    state="open",
                    author="dev",
                    author_is_maintainer=False,
                    author_is_bot=False,
                    labels=[],
                    metadata_={},
                    comments=[],
                    last_fetched_at=now,
                )
                for i in range(15)
            ]
            test_db_session.add_all(issues)
            await test_db_session.flush()

            test_db_session.add_all(
                [
                    LLMEvaluation(
                        issue_id=issues[i].id,
                        model_name="gpt-4.1",
                        summary=f"Evaluation {i}",
                        suggested_action="keep_open",
                        suggested_action_reason="reason",
                        scores={},
                        tokens_used=100,
                        prompt_tokens=60,
                        completion_tokens=40,
                        llm_backend="test",
                        evaluated_at=now - timedelta(hours=i),
                        issue_data_hash=f"hash-{i}",
                        latest=True,
                    )
                    for i in range(15)
                ]
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = client.get("/admin/recent-evaluations")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert response.text.count("Issue ") == 10
        assert "Issue 0<" in response.text
        assert "Issue 9<" in response.text
        assert "Issue 10<" not in response.text
        assert 'hx-get="/admin/recent-evaluations?offset=10' in response.text

    def test_returns_second_page(
        self,
        client: TestClient,
        test_db_session: AsyncSession,
    ) -> None:
        """Requesting offset=10 returns the remaining rows with Older disabled."""
        now = datetime(2025, 6, 21, 12, 0, tzinfo=UTC)

        async def _seed() -> None:
            project = Project(
                name="snapcraft", category="application", github_org="canonical"
            )
            test_db_session.add(project)
            await test_db_session.flush()

            issues = [
                Issue(
                    project_id=project.id,
                    source="github",
                    external_id=str(i),
                    issue_type="issue",
                    title=f"Issue {i}",
                    state="open",
                    author="dev",
                    author_is_maintainer=False,
                    author_is_bot=False,
                    labels=[],
                    metadata_={},
                    comments=[],
                    last_fetched_at=now,
                )
                for i in range(15)
            ]
            test_db_session.add_all(issues)
            await test_db_session.flush()

            test_db_session.add_all(
                [
                    LLMEvaluation(
                        issue_id=issues[i].id,
                        model_name="gpt-4.1",
                        summary=f"Evaluation {i}",
                        suggested_action="keep_open",
                        suggested_action_reason="reason",
                        scores={},
                        tokens_used=100,
                        prompt_tokens=60,
                        completion_tokens=40,
                        llm_backend="test",
                        evaluated_at=now - timedelta(hours=i),
                        issue_data_hash=f"hash-{i}",
                        latest=True,
                    )
                    for i in range(15)
                ]
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = client.get("/admin/recent-evaluations", params={"offset": 10})

        assert response.status_code == 200
        assert response.text.count("Issue ") == 5
        assert "Issue 10<" in response.text
        assert "Issue 14<" in response.text
        assert 'hx-get="/admin/recent-evaluations?offset=0' in response.text
