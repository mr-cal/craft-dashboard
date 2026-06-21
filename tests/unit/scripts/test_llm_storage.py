"""Tests for scripts.llm.storage."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from scripts.llm.storage import (
    _clear_evaluations,
    count_evaluations,
    store_evaluation_result,
)
from sqlalchemy.dialects import postgresql as pg_dialect


class _FakeExecuteResult:
    def __init__(self, *, rowcount: int = 0) -> None:
        self.rowcount = rowcount


class _RecordingSession:
    def __init__(self, *, count: int = 0, rowcount: int = 0) -> None:
        self.count = count
        self.rowcount = rowcount
        self.executed_statements: list[object] = []
        self.committed = False

    async def execute(self, stmt: object) -> _FakeExecuteResult:
        self.executed_statements.append(stmt)
        return _FakeExecuteResult(rowcount=self.rowcount)

    async def scalar(self, _stmt: object) -> int:
        return self.count

    async def commit(self) -> None:
        self.committed = True


def _session_factory(session: _RecordingSession):
    @asynccontextmanager
    async def _factory() -> AsyncIterator[_RecordingSession]:
        yield session

    return _factory


class TestStoreEvaluationResult:
    @pytest.mark.asyncio
    async def test_marks_previous_latest_false_and_upserts_new_latest(self) -> None:
        session = _RecordingSession()

        await store_evaluation_result(
            _session_factory(session),
            issue_id=42,
            result={
                "summary": "Summarized",
                "suggested_action": "keep_open",
                "suggested_action_reason": "Still active",
                "scores": {"priority": 8},
                "tokens_used": 123,
                "prompt_tokens": 45,
                "completion_tokens": 78,
                "issue_data_hash": "abc123",
            },
            model="eval-model",
            llm_backend="local",
        )

        assert session.committed is True
        assert len(session.executed_statements) == 2

        update_sql = str(
            session.executed_statements[0].compile(dialect=pg_dialect.dialect())
        )
        insert_sql = str(
            session.executed_statements[1].compile(dialect=pg_dialect.dialect())
        )

        assert "UPDATE llm_evaluations SET latest=false" in update_sql
        assert "WHERE llm_evaluations.issue_id = %(issue_id_1)s" in update_sql
        assert "INSERT INTO llm_evaluations" in insert_sql
        assert "ON CONFLICT (issue_id)" in insert_sql
        assert "DO UPDATE SET" in insert_sql
        assert "eval_version" in insert_sql
        assert "summary = excluded.summary" in insert_sql
        assert "eval_version = excluded.eval_version" in insert_sql
        assert "latest = excluded.latest" in insert_sql


class TestCountEvaluations:
    @pytest.mark.asyncio
    async def test_returns_project_scoped_count(self) -> None:
        session = _RecordingSession(count=3)
        engine = SimpleNamespace(dispose=AsyncMock())

        count = await count_evaluations(
            "snapcraft",
            session_factory=_session_factory(session),
            engine=engine,
        )

        assert count == 3
        assert session.executed_statements == []
        engine.dispose.assert_not_awaited()


class TestClearEvaluations:
    @pytest.mark.asyncio
    async def test_deletes_project_scoped_rows(self) -> None:
        session = _RecordingSession(rowcount=3)
        engine = SimpleNamespace(dispose=AsyncMock())

        deleted = await _clear_evaluations(
            project="snapcraft",
            session_factory=_session_factory(session),
            engine=engine,
        )

        assert deleted == 3
        assert session.committed is True
        assert len(session.executed_statements) == 1
        delete_sql = str(
            session.executed_statements[0].compile(dialect=pg_dialect.dialect())
        )
        assert "DELETE FROM llm_evaluations" in delete_sql
        assert "JOIN projects ON issues.project_id = projects.id" in delete_sql
        engine.dispose.assert_not_awaited()
