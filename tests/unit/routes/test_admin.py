"""Tests for admin routes."""

import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
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


class _ScalarResult:
    def __init__(self, items) -> None:
        self._items = items

    def scalars(self):
        return self._items


class _RowResult:
    def __init__(self, rows) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


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
    def __init__(self, schedules, issue_counts=None) -> None:
        self._schedules = schedules
        self._issue_counts = issue_counts or []
        self._call_count = 0

    async def execute(self, _query):
        self._call_count += 1
        # First call: RefreshSchedule query; second call: issue count query
        if self._call_count == 1:
            return _ScalarResult(self._schedules)
        return _RowResult(self._issue_counts)


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
        enable_server_eval=True,
    )
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
        assert response.headers["HX-Trigger"] == (
            '{"toast":{"message":"LLM re-evaluation has been queued for all open issues.","type":"success"}}'
        )

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
        assert "Admin: re-evaluation triggered with params:" in caplog.text

    def test_re_evaluate_returns_disabled_when_server_eval_is_off(self) -> None:
        """POST /admin/re-evaluate reports disabled when server eval is off."""
        app = _create_admin_app()
        app.state.settings.enable_server_eval = False

        with TestClient(app) as client:
            response = client.post(
                "/admin/re-evaluate",
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

        assert response.status_code == 409
        assert response.json() == {
            "status": "disabled",
            "message": (
                "Server-side evaluation is disabled (ENABLE_SERVER_EVAL=false). "
                "Use the eval client script for pull-based evaluation."
            ),
        }


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
        assert response.headers["HX-Trigger"] == (
            '{"toast":{"message":"Refresh schedule redistributed for 0 projects.","type":"success"}}'
        )

    def test_distribute_logs_schedule_summary(self, caplog) -> None:
        """POST /admin/distribute logs how many schedules were redistributed."""
        schedules = [
            SimpleNamespace(project_id=1, source="github", next_refresh_at=None),
            SimpleNamespace(project_id=2, source="github", next_refresh_at=None),
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

    def test_distribute_times_align_with_calendar_day_buckets(self) -> None:
        """All next_refresh_at times fall within midnight-aligned calendar windows.

        This is the key correctness property: the distributed times must match
        the same midnight-aligned buckets that get_schedule_day_counts uses,
        otherwise the admin panel shows the wrong counts.
        """
        schedules = [
            SimpleNamespace(project_id=i, source="github", next_refresh_at=None)
            for i in range(10)
        ]

        async def _override_distribute_session():
            yield _DistributeSession(schedules)

        app = _create_admin_app(session_override=_override_distribute_session)
        before = datetime.now(UTC)

        with TestClient(app) as client:
            client.post(
                "/admin/distribute",
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

        now = before
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_midnight = today_midnight + timedelta(days=1)

        times = [s.next_refresh_at for s in schedules]
        assert all(t is not None for t in times)
        assert len(set(times)) == 10

        for t in times:
            # Must be on or after tomorrow midnight — nothing scheduled for today.
            assert t >= tomorrow_midnight, (
                f"{t} is before tomorrow midnight {tomorrow_midnight}"
            )
            # Must be within the refresh window.
            assert t < tomorrow_midnight + timedelta(days=8)
            # Must be a whole number of calendar days from tomorrow midnight
            # (i.e., within some valid day bucket).
            day_offset = (
                t.replace(hour=0, minute=0, second=0, microsecond=0) - tomorrow_midnight
            ).days
            assert 0 <= day_offset < 7, f"Day offset {day_offset} out of range for {t}"
            # Must be before 2 AM UTC on its calendar day so the nightly 2 AM UTC
            # cron always picks up every project scheduled for that day.
            day_midnight = t.replace(hour=0, minute=0, second=0, microsecond=0)
            assert t < day_midnight + timedelta(hours=2), (
                f"{t} is at or after 2 AM UTC — cron will miss it until the next day"
            )

    def test_distribute_balances_heavy_and_light_projects(self) -> None:
        """Heavy and light projects land on different calendar days."""
        schedules = [
            SimpleNamespace(project_id=1, source="github", next_refresh_at=None),
            SimpleNamespace(project_id=2, source="github", next_refresh_at=None),
        ]
        issue_counts = [
            SimpleNamespace(project_id=1, source="github", issue_count=10),
            SimpleNamespace(project_id=2, source="github", issue_count=90),
        ]

        async def _override_distribute_session():
            yield _DistributeSession(schedules, issue_counts)

        app = _create_admin_app(session_override=_override_distribute_session)

        with TestClient(app) as client:
            client.post(
                "/admin/distribute",
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

        t1, t2 = schedules[0].next_refresh_at, schedules[1].next_refresh_at
        assert t1 is not None
        assert t2 is not None
        # Two projects on separate calendar days, not bunched on the same day.
        assert t1.date() != t2.date()

    def test_distribute_balances_total_issues_across_days(self) -> None:
        """Greedy packing keeps per-calendar-day issue totals roughly equal.

        Uses the same midnight-aligned bucketing as get_schedule_day_counts so
        we'd catch a calendar-day misalignment before it shows up in the UI.
        """
        schedules = [
            SimpleNamespace(project_id=i, source="github", next_refresh_at=None)
            for i in range(7)
        ]
        issue_counts = [
            SimpleNamespace(project_id=0, source="github", issue_count=1000),
            *[
                SimpleNamespace(project_id=i, source="github", issue_count=170)
                for i in range(1, 7)
            ],
        ]

        async def _override_distribute_session():
            yield _DistributeSession(schedules, issue_counts)

        app = _create_admin_app(session_override=_override_distribute_session)
        before = datetime.now(UTC)

        with TestClient(app) as client:
            client.post(
                "/admin/distribute",
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

        # Bucket by calendar day (midnight-aligned), same as get_schedule_day_counts.
        now = before
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_midnight = today_midnight + timedelta(days=1)
        issue_map = {0: 1000, **dict.fromkeys(range(1, 7), 170)}
        day_issues: dict[int, int] = {}
        for s in schedules:
            cal_day = (
                s.next_refresh_at.replace(hour=0, minute=0, second=0, microsecond=0)
                - tomorrow_midnight
            ).days
            day_issues[cal_day] = day_issues.get(cal_day, 0) + issue_map[s.project_id]

        # No single day may hold more than 60 % of all issues.
        total = sum(day_issues.values())
        for day, count in day_issues.items():
            assert count / total < 0.6, (
                f"Calendar day +{day} has {count}/{total} issues ({count / total:.0%})"
            )


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

    def test_admin_page_includes_system_status_panel(self) -> None:
        """GET /admin shows the HTMX-powered system status panel."""
        app = _create_admin_app()

        with TestClient(app) as client:
            response = client.get("/admin")

        assert response.status_code == 200
        assert "System Status" in response.text
        assert 'hx-get="/admin/status"' in response.text
        assert 'hx-trigger="load, every 60s"' in response.text
        assert 'data-system-status-kind="collection"' in response.text
        assert 'data-system-status-kind="evaluation"' in response.text
