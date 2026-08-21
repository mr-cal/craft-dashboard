"""Tests for scripts.backfill_search_embeddings."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from scripts import backfill_search_embeddings


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


def test_build_search_embedding_text_combines_title_and_body() -> None:
    text = backfill_search_embeddings.build_search_embedding_text(
        "Issue title", "Issue body"
    )

    assert text == "Issue title\n\nIssue body"


def test_build_search_embedding_text_handles_missing_body() -> None:
    text = backfill_search_embeddings.build_search_embedding_text("Issue title", None)

    assert text == "Issue title\n\n"


def test_build_search_embedding_text_truncates_huge_bodies() -> None:
    huge_body = "x" * 100_000
    text = backfill_search_embeddings.build_search_embedding_text(
        "Issue title", huge_body
    )

    assert len(text) == backfill_search_embeddings._MAX_EMBEDDING_TEXT_CHARS


@pytest.mark.asyncio
async def test_run_backfill_uses_openrouter_batch_embeddings(monkeypatch) -> None:
    engine = MagicMock()
    engine.dispose = AsyncMock()
    rows = [(1, "Issue title", "Issue body")]
    session_factory = _FakeSessionFactory(rows)
    embedding_client = MagicMock()
    embedding_client.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
    embedding_client.close = AsyncMock()

    monkeypatch.setattr(
        backfill_search_embeddings,
        "create_async_engine",
        MagicMock(return_value=engine),
    )
    monkeypatch.setattr(
        backfill_search_embeddings,
        "sessionmaker",
        MagicMock(return_value=session_factory),
    )
    monkeypatch.setattr(
        backfill_search_embeddings,
        "EmbeddingClient",
        MagicMock(return_value=embedding_client),
    )

    await backfill_search_embeddings.run_backfill(
        database_url="postgresql+asyncpg://localhost/test",
        openrouter_api_key="test-openrouter-key",
        embedding_model="openai/text-embedding-3-small",
        batch_size=100,
        limit=0,
        dry_run=False,
    )

    backfill_search_embeddings.EmbeddingClient.assert_called_once_with(
        base_url="https://openrouter.ai/api/v1",
        model="openai/text-embedding-3-small",
        api_key="test-openrouter-key",
        ca_cert="",
    )
    embedding_client.embed_batch.assert_awaited_once_with(
        ["Issue title\n\nIssue body"], dimensions=1024
    )


@pytest.mark.asyncio
async def test_run_backfill_dry_run_does_not_embed(monkeypatch) -> None:
    engine = MagicMock()
    engine.dispose = AsyncMock()
    rows = [(1, "Issue title", "Issue body")]
    session_factory = _FakeSessionFactory(rows)
    embedding_client = MagicMock()
    embedding_client.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
    embedding_client.close = AsyncMock()

    monkeypatch.setattr(
        backfill_search_embeddings,
        "create_async_engine",
        MagicMock(return_value=engine),
    )
    monkeypatch.setattr(
        backfill_search_embeddings,
        "sessionmaker",
        MagicMock(return_value=session_factory),
    )
    monkeypatch.setattr(
        backfill_search_embeddings,
        "EmbeddingClient",
        MagicMock(return_value=embedding_client),
    )

    await backfill_search_embeddings.run_backfill(
        database_url="postgresql+asyncpg://localhost/test",
        openrouter_api_key="test-openrouter-key",
        embedding_model="openai/text-embedding-3-small",
        batch_size=100,
        limit=0,
        dry_run=True,
    )

    embedding_client.embed_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_backfill_skips_row_that_fails_even_at_per_row_level(
    monkeypatch,
) -> None:
    """A row whose embedding call fails even after batch fallback is skipped, not fatal."""
    engine = MagicMock()
    engine.dispose = AsyncMock()
    rows = [
        (1, "Good issue", "body"),
        (2, "Bad issue", "body"),
        (3, "Another good issue", "body"),
    ]
    session_factory = _FakeSessionFactory(rows)
    embedding_client = MagicMock()
    embedding_client.embed_batch = AsyncMock(
        side_effect=RuntimeError("batch embedding failed")
    )

    async def _embed(text, dimensions):
        del dimensions
        if "Bad issue" in text:
            raise RuntimeError("per-row embedding failed")
        return [0.1, 0.2, 0.3]

    embedding_client.embed = AsyncMock(side_effect=_embed)
    embedding_client.close = AsyncMock()

    monkeypatch.setattr(
        backfill_search_embeddings,
        "create_async_engine",
        MagicMock(return_value=engine),
    )
    monkeypatch.setattr(
        backfill_search_embeddings,
        "sessionmaker",
        MagicMock(return_value=session_factory),
    )
    monkeypatch.setattr(
        backfill_search_embeddings,
        "EmbeddingClient",
        MagicMock(return_value=embedding_client),
    )

    await backfill_search_embeddings.run_backfill(
        database_url="postgresql+asyncpg://localhost/test",
        openrouter_api_key="test-openrouter-key",
        embedding_model="openai/text-embedding-3-small",
        batch_size=100,
        limit=0,
        dry_run=False,
    )

    # The two good rows are embedded and persisted; the bad row is skipped
    # rather than crashing the whole backfill run.
    assert embedding_client.embed.await_count == 3
