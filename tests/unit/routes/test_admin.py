"""Tests for admin routes."""

from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from fastapi.testclient import TestClient


class _AdminSession:
    async def execute(self, _query):
        return []


async def _override_admin_db_session():
    yield _AdminSession()


class TestAdminRoutes:
    """Tests for admin routes."""

    def test_admin_refresh_requires_auth(self) -> None:
        """POST /admin/refresh returns 401 without token."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_admin_db_session

        with TestClient(app) as client:
            response = client.post("/admin/refresh")

        assert response.status_code == 401

    def test_admin_refresh_rejects_bad_token(self) -> None:
        """POST /admin/refresh returns 401 with wrong token."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_admin_db_session

        with TestClient(app) as client:
            response = client.post(
                "/admin/refresh",
                headers={"Authorization": "Bearer wrong-token"},
            )

        assert response.status_code == 401

    def test_admin_health_requires_auth(self) -> None:
        """GET /admin/health returns 401 without token."""
        app = create_app()
        app.dependency_overrides[get_db_session] = _override_admin_db_session

        with TestClient(app) as client:
            response = client.get("/admin/health")

        assert response.status_code == 401
