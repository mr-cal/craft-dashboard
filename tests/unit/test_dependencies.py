"""Tests for FastAPI dependency injection helpers."""

import inspect
from unittest.mock import MagicMock

import pytest
from craft_dashboard import dependencies
from craft_dashboard.dependencies import get_db_session, set_session_factory


class TestGetDbSession:
    """Tests for get_db_session."""

    def test_get_db_session_is_async_generator(self) -> None:
        """get_db_session is an async generator function."""
        assert inspect.isasyncgenfunction(get_db_session)


class TestGetDbSessionError:
    async def test_raises_when_not_initialized(self) -> None:
        """get_db_session raises RuntimeError when factory is not set."""
        original_factory = dependencies._session_factory
        try:
            dependencies._session_factory = None
            gen = get_db_session()
            with pytest.raises(RuntimeError, match="not initialized"):
                await gen.__anext__()
        finally:
            dependencies._session_factory = original_factory


class TestModuleLevelVariable:
    def test_set_and_get_factory(self) -> None:
        """set_session_factory stores value retrievable by get_db_session."""
        mock_factory = MagicMock()
        original_factory = dependencies._session_factory
        try:
            dependencies._session_factory = None
            set_session_factory(mock_factory)
            assert dependencies._session_factory is mock_factory
        finally:
            dependencies._session_factory = original_factory
