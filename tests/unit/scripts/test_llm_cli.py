"""Tests for scripts.llm.cli."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner
from scripts.llm.cli import _clear_main, cli


class TestClearEvaluationsCommand:
    def test_help_lists_clear_evaluations_command(self) -> None:
        runner = CliRunner()

        result = runner.invoke(cli, ["clear-evaluations", "--help"])

        assert result.exit_code == 0
        assert "--project" in result.output
        assert "--yes" in result.output

    @pytest.mark.asyncio
    async def test_clear_main_deletes_rows_without_storage_helpers(
        self, monkeypatch
    ) -> None:
        class _FakeResult:
            rowcount = 3

        class _FakeSession:
            def __init__(self) -> None:
                self.scalar_calls = []
                self.execute_calls = []
                self.committed = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def scalar(self, query):
                self.scalar_calls.append(query)
                return 3

            async def execute(self, query):
                self.execute_calls.append(query)
                return _FakeResult()

            async def commit(self) -> None:
                self.committed = True

        class _FakeSessionFactory:
            def __init__(self, session) -> None:
                self._session = session

            def __call__(self):
                return self._session

        fake_session = _FakeSession()
        session_factory = _FakeSessionFactory(fake_session)
        confirm = MagicMock()
        engine = MagicMock()
        engine.dispose = AsyncMock()
        monkeypatch.delattr("scripts.llm.cli.count_evaluations", raising=False)
        monkeypatch.delattr("scripts.llm.cli._clear_evaluations", raising=False)
        monkeypatch.setattr("scripts.llm.cli.click.confirm", confirm)
        monkeypatch.setattr(
            "scripts.llm.cli.get_engine", MagicMock(return_value=engine)
        )
        monkeypatch.setattr(
            "scripts.llm.cli.get_session_factory",
            MagicMock(return_value=session_factory),
        )

        await _clear_main(project="snapcraft", yes=False)

        assert len(fake_session.scalar_calls) == 1
        assert len(fake_session.execute_calls) == 1
        confirm.assert_called_once()
        assert fake_session.committed is True
        engine.dispose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_clear_main_skips_delete_when_no_evaluations(
        self, monkeypatch
    ) -> None:
        class _FakeSession:
            def __init__(self) -> None:
                self.execute_calls = []
                self.committed = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def scalar(self, query):
                return 0

            async def execute(self, query):
                self.execute_calls.append(query)
                raise AssertionError("delete should not run")

            async def commit(self) -> None:
                self.committed = True

        class _FakeSessionFactory:
            def __init__(self, session) -> None:
                self._session = session

            def __call__(self):
                return self._session

        fake_session = _FakeSession()
        engine = MagicMock()
        engine.dispose = AsyncMock()
        confirm = MagicMock()
        monkeypatch.setattr("scripts.llm.cli.click.confirm", confirm)
        monkeypatch.setattr(
            "scripts.llm.cli.get_engine", MagicMock(return_value=engine)
        )
        monkeypatch.setattr(
            "scripts.llm.cli.get_session_factory",
            MagicMock(return_value=_FakeSessionFactory(fake_session)),
        )

        await _clear_main(project="", yes=False)

        confirm.assert_not_called()
        assert fake_session.execute_calls == []
        assert fake_session.committed is False
        engine.dispose.assert_awaited_once()
