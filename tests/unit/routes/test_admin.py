"""Tests for admin routes."""

import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.routes.admin import _verify_origin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from github import GithubException
from starlette.exceptions import HTTPException

_ADMIN_TOKEN = "test-token-123"


class _EmptyResult:
    def scalars(self):
        return []

    def __iter__(self):
        return iter(())

    def scalar_one(self):
        return 0

    def one(self):
        return SimpleNamespace(
            evaluations=0,
            tokens=0,
            prompt_tokens=0,
            completion_tokens=0,
            total_evaluations=0,
            total_tokens=0,
            total_prompt_tokens=0,
            total_completion_tokens=0,
            recent_evaluations=0,
            recent_tokens=0,
            recent_prompt_tokens=0,
            recent_completion_tokens=0,
        )


class _AdminSession:
    async def scalar(self, _query):
        return 0

    async def execute(self, _query):
        return _EmptyResult()

    async def commit(self) -> None:
        return None


class _HealthFailureSession(_AdminSession):
    def __init__(self) -> None:
        self._health_check_seen = False

    async def execute(self, query):
        if not self._health_check_seen and str(query) == "SELECT 1":
            self._health_check_seen = True
            msg = "database password leaked"
            raise RuntimeError(msg)
        return await super().execute(query)


async def _override_admin_db_session():
    yield _AdminSession()


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


def _create_admin_app(session_override=_override_admin_db_session):
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.state.settings = SimpleNamespace(
        admin_token=_ADMIN_TOKEN,
        refresh_age_days=7,
    )
    app.dependency_overrides[get_db_session] = session_override
    return app


def _stub_admin_page_metrics(
    monkeypatch: pytest.MonkeyPatch,
    *,
    recent_activity=None,
    api_budget=None,
    api_budget_exception: Exception | None = None,
    next_expected_fetch=None,
) -> None:
    async def _fake_recent_activity(
        self,
        limit: int = 50,
        offset: int = 0,
        filtered_issues: dict | None = None,
    ):
        assert limit == 10
        assert offset == 0
        activity = recent_activity if recent_activity is not None else []
        return activity, len(activity)

    async def _fake_api_budget(self):
        if api_budget_exception is not None:
            raise api_budget_exception
        return api_budget or {
            "core_remaining": 5000,
            "core_limit": 5000,
            "core_reset": datetime(2025, 1, 10, 13, 0, tzinfo=UTC),
            "graphql_remaining": 5000,
            "graphql_limit": 5000,
            "graphql_reset": datetime(2025, 1, 10, 13, 0, tzinfo=UTC),
        }

    async def _fake_next_expected_fetch(self):
        return next_expected_fetch

    monkeypatch.setattr(
        "craft_dashboard.routes.admin.AdminService.get_recent_issue_activity",
        _fake_recent_activity,
    )
    monkeypatch.setattr(
        "craft_dashboard.routes.admin.AdminService.get_api_budget",
        _fake_api_budget,
    )
    monkeypatch.setattr(
        "craft_dashboard.routes.admin.AdminService.get_next_expected_fetch",
        _fake_next_expected_fetch,
    )


class TestAdminRoutes:
    """Tests for admin routes."""

    def test_admin_refresh_requires_auth(self) -> None:
        """POST /admin/refresh returns 401 without token."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.post("/admin/refresh")

        assert response.status_code == 401

    def test_admin_refresh_rejects_bad_token(self) -> None:
        """POST /admin/refresh returns 401 with wrong token."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.post(
                "/admin/refresh",
                headers={"Authorization": "Bearer wrong-token"},
            )

        assert response.status_code == 401

    def test_admin_health_requires_auth(self) -> None:
        """GET /admin/health returns 401 without token."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.get("/admin/health")

        assert response.status_code == 401

    def test_admin_health_hides_database_error_details(self) -> None:
        """GET /admin/health returns a sanitized database error."""

        async def _override_failing_session():
            yield _HealthFailureSession()

        app = _create_admin_app(session_override=_override_failing_session)

        with TestClient(app) as client:
            response = client.get(
                "/admin/health",
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert response.json()["database"] == "error"
        assert "database password leaked" not in response.text


class TestAdminRefreshWithAuth:
    """Authenticated tests for admin routes."""

    def test_refresh_with_valid_token(self) -> None:
        """POST /admin/refresh accepts the configured admin token."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.post(
                "/admin/refresh",
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

        assert response.status_code == 202
        assert response.json()["status"] == "refresh_queued"
        assert "Data refresh has been queued" in response.headers["HX-Trigger"]

    def test_refresh_logs_when_queued(self, caplog) -> None:
        """POST /admin/refresh emits an audit log when work is queued."""
        app = _create_admin_app()

        with caplog.at_level(logging.INFO, logger="craft_dashboard.routes.admin"):
            with TestClient(app) as client:
                response = client.post(
                    "/admin/refresh",
                    headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
                )

        assert response.status_code == 202
        assert "Admin: refresh queued" in caplog.text

    def test_refresh_spawns_collect_data_subprocess(self, monkeypatch) -> None:
        """POST /admin/refresh spawns collect_data.py as a subprocess."""
        app = _create_admin_app()
        seen: dict[str, object] = {}

        class _FakeProcess:
            pass

        async def _fake_create_subprocess_exec(*args, **kwargs):
            seen["args"] = args
            return _FakeProcess()

        monkeypatch.setattr(
            "craft_dashboard.routes.admin.asyncio.create_subprocess_exec",
            _fake_create_subprocess_exec,
        )

        with TestClient(app) as client:
            response = client.post(
                "/admin/refresh",
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

        assert response.status_code == 202
        assert "collect_data.py" in seen.get("args", ("",))[1]
        assert seen["args"][0] == sys.executable

    def test_re_evaluate_route_is_removed(self) -> None:
        """POST /admin/re-evaluate is no longer exposed."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.post("/admin/re-evaluate")

        assert response.status_code == 404

    def test_admin_page_omits_re_evaluate_controls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /admin no longer renders the re-evaluate UI."""
        app = _create_admin_app()
        _stub_admin_page_metrics(monkeypatch)

        with TestClient(app) as client:
            response = client.get("/admin")

        assert response.status_code == 200
        assert "Re-evaluate issues" not in response.text
        assert "triggerEvaluate" not in response.text
        assert "triggerDryRun" not in response.text


class TestAdminLogs:
    """Tests for the admin logs endpoint."""

    def test_logs_requires_auth(self) -> None:
        """GET /admin/logs returns 401 without token."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.get("/admin/logs")

        assert response.status_code == 401

    def test_logs_returns_text(self) -> None:
        """GET /admin/logs returns plain text when authorized."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.get(
                "/admin/logs",
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]


class TestVerifyOrigin:
    def test_same_origin_passes(self) -> None:
        """Requests from the same origin pass."""
        request = MagicMock()
        request.headers = {
            "origin": "https://dashboard.example.com",
            "host": "dashboard.example.com",
        }
        _verify_origin(request)

    def test_cross_origin_rejected(self) -> None:
        """Cross-origin requests are rejected."""
        request = MagicMock()
        request.headers = {
            "origin": "https://evil.com",
            "host": "dashboard.example.com",
        }
        with pytest.raises(HTTPException) as exc_info:
            _verify_origin(request)
        assert exc_info.value.status_code == 403

    def test_no_origin_header_passes(self) -> None:
        """API clients without Origin header pass."""
        request = MagicMock()
        headers = MagicMock()
        headers.get = lambda key, default="": {"host": "dashboard.example.com"}.get(
            key, default
        )
        request.headers = headers
        _verify_origin(request)


class TestAdminPage:
    """Tests for the admin dashboard page."""

    def test_admin_page_no_auth_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET /admin renders without admin authentication."""
        app = _create_admin_app()
        _stub_admin_page_metrics(monkeypatch)

        with TestClient(app) as client:
            response = client.get("/admin")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Admin" in response.text

    def test_admin_page_includes_system_status_panel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /admin shows the HTMX-powered system status panel."""
        app = _create_admin_app()
        _stub_admin_page_metrics(monkeypatch)

        with TestClient(app) as client:
            response = client.get("/admin")

        assert response.status_code == 200
        assert "System status" in response.text
        assert 'hx-get="/admin/status"' in response.text
        assert 'hx-trigger="load, every 60s"' in response.text
        assert 'data-system-status-kind="collection"' in response.text
        assert 'data-system-status-kind="evaluation"' in response.text

    def test_admin_page_renders_api_budget_recent_activity_and_next_fetch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /admin shows API budgets, next fetch, and recent issue activity."""
        app = _create_admin_app()
        _stub_admin_page_metrics(
            monkeypatch,
            recent_activity=[
                {
                    "occurred_at": datetime(2025, 1, 10, 12, 34, tzinfo=UTC),
                    "number": "42",
                    "change_type": "closed",
                    "project": "snapcraft",
                    "title": "Closed after merge",
                    "url": "https://github.com/canonical/snapcraft/issues/42",
                    "issue_type": "pull_request",
                }
            ],
            api_budget={
                "core_remaining": 4999,
                "core_limit": 5000,
                "core_reset": datetime(2025, 1, 10, 13, 0, tzinfo=UTC),
                "graphql_remaining": 4998,
                "graphql_limit": 5000,
                "graphql_reset": datetime(2025, 1, 10, 13, 0, tzinfo=UTC),
            },
            next_expected_fetch=datetime(2025, 1, 10, 12, 40, tzinfo=UTC),
        )

        with TestClient(app) as client:
            response = client.get("/admin")

        assert response.status_code == 200
        assert "Recent activity" in response.text
        assert "REST API budget" in response.text
        assert "4999 / 5000 remaining" in response.text
        assert "GraphQL API budget" in response.text
        assert "4998 / 5000 remaining" in response.text
        assert "Next open-issue fetch" in response.text
        assert "2025-01-10 12:40 PM UTC" in response.text
        assert "Closed after merge" in response.text
        assert "#42" in response.text
        assert "#42" in response.text
        assert "closed" in response.text
        assert "Closed after merge" in response.text

    def test_admin_page_shows_unknown_when_next_fetch_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /admin shows Unknown when no next open-issue fetch is available."""
        app = _create_admin_app()
        _stub_admin_page_metrics(
            monkeypatch,
            api_budget={
                "core_remaining": 100,
                "core_limit": 5000,
                "core_reset": datetime(2025, 1, 10, 13, 0, tzinfo=UTC),
                "graphql_remaining": 200,
                "graphql_limit": 5000,
                "graphql_reset": datetime(2025, 1, 10, 13, 0, tzinfo=UTC),
            },
        )

        with TestClient(app) as client:
            response = client.get("/admin")

        assert response.status_code == 200
        assert "Next open-issue fetch" in response.text
        assert "Unknown" in response.text

    def test_admin_page_handles_api_budget_lookup_failures(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """GET /admin keeps rendering when the API budget lookup fails."""
        app = _create_admin_app()
        _stub_admin_page_metrics(
            monkeypatch,
            api_budget_exception=GithubException(401, {"message": "Bad credentials"}),
        )

        with caplog.at_level(logging.WARNING, logger="craft_dashboard.routes.admin"):
            with TestClient(app) as client:
                response = client.get("/admin")

        assert response.status_code == 200
        assert "REST API budget" in response.text
        assert "GraphQL API budget" in response.text
        assert response.text.count("Unknown") >= 2
        assert "Admin page: API budget lookup failed" in caplog.text
