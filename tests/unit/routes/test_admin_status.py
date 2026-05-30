"""Tests for the admin status endpoint."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _AdminSession:
    async def execute(self, _query):
        return []


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


@pytest.fixture
def test_client() -> AsyncGenerator[TestClient, None]:
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.state.settings = SimpleNamespace(refresh_age_days=7)
    app.state.settings.admin_token = "test-token"

    async def _override_session() -> AsyncGenerator[_AdminSession, None]:
        yield _AdminSession()

    app.dependency_overrides[get_db_session] = _override_session

    with TestClient(app) as client:
        yield client


class TestAdminStatusRoute:
    def test_status_returns_public_system_status(self, test_client: TestClient) -> None:
        now = datetime(2025, 1, 12, 15, 0, tzinfo=UTC)
        payload = {
            "collection_running": True,
            "evaluation_running": False,
            "last_collection": now,
            "last_evaluation": now,
        }

        with patch(
            "craft_dashboard.routes.admin.AdminService.get_system_status",
            AsyncMock(return_value=payload),
        ) as get_status:
            response = test_client.get("/admin/status")

        assert response.status_code == 200
        assert response.json() == {
            "collection_running": True,
            "evaluation_running": False,
            "last_collection": "2025-01-12T15:00:00+00:00",
            "last_evaluation": "2025-01-12T15:00:00+00:00",
        }
        get_status.assert_awaited_once()

    def test_status_does_not_require_admin_auth(self, test_client: TestClient) -> None:
        with patch(
            "craft_dashboard.routes.admin.AdminService.get_system_status",
            AsyncMock(
                return_value={
                    "collection_running": False,
                    "evaluation_running": False,
                    "last_collection": None,
                    "last_evaluation": None,
                }
            ),
        ):
            response = test_client.get("/admin/status")

        assert response.status_code == 200
