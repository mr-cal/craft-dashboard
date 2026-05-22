"""Tests for FastAPI dependency injection helpers."""

import inspect

import pytest

from craft_dashboard.dependencies import _session_factory_var, get_db_session


class TestGetDbSession:
    """Tests for get_db_session."""

    def test_get_db_session_is_async_generator(self) -> None:
        """get_db_session is an async generator function."""
        assert inspect.isasyncgenfunction(get_db_session)


class TestGetDbSessionError:
    async def test_raises_when_not_initialized(self) -> None:
        """get_db_session raises RuntimeError when factory is not set."""
        token = _session_factory_var.set(None)
        try:
            gen = get_db_session()
            with pytest.raises(RuntimeError, match="not initialized"):
                await gen.__anext__()
        finally:
            _session_factory_var.reset(token)


class TestContextVarIsolation:
    def test_set_and_get_factory(self) -> None:
        """set_session_factory stores value retrievable by get_db_session."""
        from unittest.mock import MagicMock

        from craft_dashboard.dependencies import set_session_factory

        mock_factory = MagicMock()
        token = _session_factory_var.set(None)
        try:
            set_session_factory(mock_factory)
            assert _session_factory_var.get() is mock_factory
        finally:
            _session_factory_var.reset(token)
