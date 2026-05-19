"""Tests for FastAPI dependency injection helpers."""

import inspect

from craft_dashboard.dependencies import get_db_session


class TestGetDbSession:
    """Tests for get_db_session."""

    def test_get_db_session_is_async_generator(self) -> None:
        """get_db_session is an async generator function."""
        assert inspect.isasyncgenfunction(get_db_session)
