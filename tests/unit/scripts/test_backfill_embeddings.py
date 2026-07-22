"""Tests for scripts.backfill_embeddings."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from scripts import backfill_embeddings


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.execute_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False

    async def execute(self, query):
        self.execute_calls.append(query)
        if len(self.execute_calls) == 1:
            return _FakeExecuteResult(self.rows)
        return MagicMock()

    def begin(self):
        return self


class _FakeSessionFactory:
    def __init__(self, rows):
        self.rows = rows
        self.sessions = []
        self._delivered_rows = False

    def __call__(self):
        rows = [] if self._delivered_rows else self.rows
        self._delivered_rows = True
        session = _FakeSession(rows)
        self.sessions.append(session)
        return session


@pytest.mark.asyncio
async def test_run_backfill_uses_openrouter_batch_embeddings(monkeypatch) -> None:
    engine = MagicMock()
    engine.dispose = AsyncMock()
    rows = [(1, "Issue title", "Issue summary")]
    session_factory = _FakeSessionFactory(rows)
    embedding_client = MagicMock()
    embedding_client.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
    embedding_client.close = AsyncMock()

    monkeypatch.setattr(
        backfill_embeddings,
        "create_async_engine",
        MagicMock(return_value=engine),
    )
    monkeypatch.setattr(
        backfill_embeddings,
        "sessionmaker",
        MagicMock(return_value=session_factory),
    )
    monkeypatch.setattr(
        backfill_embeddings,
        "EmbeddingClient",
        MagicMock(return_value=embedding_client),
    )

    await backfill_embeddings.run_backfill(
        database_url="postgresql+asyncpg://localhost/test",
        openrouter_api_key="test-openrouter-key",
        embedding_model="openai/text-embedding-3-small",
        batch_size=100,
        limit=0,
        dry_run=False,
    )

    backfill_embeddings.EmbeddingClient.assert_called_once_with(
        base_url="https://openrouter.ai/api/v1",
        model="openai/text-embedding-3-small",
        api_key="test-openrouter-key",
        ca_cert="",
    )
    embedding_client.embed_batch.assert_awaited_once_with(
        ["Issue title. Issue summary"], dimensions=1024
    )
