"""Tests for admin routes."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.routes.admin import _verify_origin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException

_ADMIN_TOKEN = "test-token-123"


class _EmptyResult:
    def scalars(self):
        return []

    def __iter__(self):
        return iter(())


class _ScalarResult:
    def __init__(self, items) -> None:
        self._items = items

    def scalars(self):
        return self._items


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


class _DistributeSession(_AdminSession):
    def __init__(self, schedules) -> None:
        self._schedules = schedules

    async def execute(self, _query):
        return _ScalarResult(self._schedules)


async def _override_admin_db_session():
    yield _AdminSession()


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


def _create_admin_app(session_override=_override_admin_db_session):
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.state.settings = SimpleNamespace(admin_token=_ADMIN_TOKEN, refresh_age_days=7)
    app.dependency_overrides[get_db_session] = session_override
    return app


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
    """Authenticated tests for refresh and re-evaluate routes."""

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

    def test_re_evaluate_requires_auth(self) -> None:
        """POST /admin/re-evaluate returns 401 without token."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.post("/admin/re-evaluate")

        assert response.status_code == 401

    def test_re_evaluate_with_valid_token(self) -> None:
        """POST /admin/re-evaluate accepts the configured admin token."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.post(
                "/admin/re-evaluate",
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

        assert response.status_code == 202
        assert response.json()["status"] == "evaluation_queued"

    def test_re_evaluate_logs_when_queued(self, caplog) -> None:
        """POST /admin/re-evaluate emits an audit log when work is queued."""
        app = _create_admin_app()

        with caplog.at_level(logging.INFO, logger="craft_dashboard.routes.admin"):
            with TestClient(app) as client:
                response = client.post(
                    "/admin/re-evaluate",
                    headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
                )

        assert response.status_code == 202
        assert "Admin: re-evaluation queued" in caplog.text


class TestAdminDistribute:
    """Tests for refresh schedule distribution."""

    def test_distribute_requires_auth(self) -> None:
        """POST /admin/distribute returns 401 without token."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.post("/admin/distribute")

        assert response.status_code == 401

    def test_distribute_empty_schedules(self) -> None:
        """POST /admin/distribute reports zero schedules when none exist."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.post(
                "/admin/distribute",
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

        assert response.status_code == 200
        assert response.json()["count"] == 0

    def test_distribute_logs_schedule_summary(self, caplog) -> None:
        """POST /admin/distribute logs how many schedules were redistributed."""
        schedules = [
            SimpleNamespace(next_refresh_at=None),
            SimpleNamespace(next_refresh_at=None),
        ]

        async def _override_distribute_session():
            yield _DistributeSession(schedules)

        app = _create_admin_app(session_override=_override_distribute_session)

        with caplog.at_level(logging.INFO, logger="craft_dashboard.routes.admin"):
            with TestClient(app) as client:
                response = client.post(
                    "/admin/distribute",
                    headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
                )

        assert response.status_code == 200
        assert "Admin: distributed 2 schedules over 7 days" in caplog.text


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

    def test_admin_page_no_auth_required(self) -> None:
        """GET /admin renders without admin authentication."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.get("/admin")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Admin" in response.text
