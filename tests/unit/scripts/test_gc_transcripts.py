"""Tests for scripts.gc_transcripts."""

from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3] / "scripts" / "gc_transcripts.py"
)
SPEC = importlib.util.spec_from_file_location("gc_transcripts_script", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
gc_transcripts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gc_transcripts)


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False


class _FakeSessionFactory:
    def __init__(self, session) -> None:
        self._session = session

    def __call__(self):
        return self._session


@pytest.mark.asyncio
async def test_main_uses_configured_retention_days(monkeypatch) -> None:
    fake_session = _FakeSession()
    session_factory = _FakeSessionFactory(fake_session)
    engine = MagicMock()
    engine.dispose = AsyncMock()
    settings = SimpleNamespace(
        database_url="postgresql+asyncpg://localhost/craft_dashboard",
        db_pool_size=5,
        db_max_overflow=10,
        eval_transcript_retention_days=17,
    )
    delete_superseded_transcripts = AsyncMock(return_value=4)
    info_log = MagicMock()

    monkeypatch.setattr(gc_transcripts, "Settings", lambda: settings)
    monkeypatch.setattr(
        gc_transcripts,
        "get_engine",
        MagicMock(return_value=engine),
    )
    monkeypatch.setattr(
        gc_transcripts,
        "get_session_factory",
        MagicMock(return_value=session_factory),
    )
    monkeypatch.setattr(
        gc_transcripts,
        "delete_superseded_transcripts",
        delete_superseded_transcripts,
    )
    monkeypatch.setattr(gc_transcripts.logger, "info", info_log)

    await gc_transcripts._main()

    gc_transcripts.get_engine.assert_called_once_with(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    gc_transcripts.get_session_factory.assert_called_once_with(engine)
    delete_superseded_transcripts.assert_awaited_once_with(
        fake_session,
        retention_days=settings.eval_transcript_retention_days,
    )
    info_log.assert_called_once_with(
        "Transcript GC: deleted %d superseded transcript(s)",
        4,
    )
    engine.dispose.assert_awaited_once()
