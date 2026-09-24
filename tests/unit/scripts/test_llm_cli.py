"""Tests for scripts.llm.cli."""

from unittest.mock import AsyncMock, MagicMock

import click
import httpx
import pytest
from click.testing import CliRunner
from scripts.llm.cli import (
    _clear_main,
    _count_evaluations,
    _delete_evaluations,
    cli,
)


class TestClearEvaluationsCommand:
    def test_help_lists_clear_evaluations_command(self) -> None:
        runner = CliRunner()

        result = runner.invoke(cli, ["clear-evaluations", "--help"])

        assert result.exit_code == 0
        assert "--project" in result.output
        assert "--yes" in result.output

    @pytest.mark.asyncio
    async def test_clear_main_missing_auth_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DASHBOARD_URL", raising=False)
        monkeypatch.delenv("EVAL_API_TOKEN", raising=False)
        monkeypatch.setenv("LLM_CONFIG_ERROR_DELAY_SECONDS", "0")

        with pytest.raises(click.UsageError, match="Missing required authentication"):
            await _clear_main(project="", yes=True)

    @pytest.mark.asyncio
    async def test_clear_main_deletes_rows_via_api(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DASHBOARD_URL", "http://testserver")
        monkeypatch.setenv("EVAL_API_TOKEN", "test-token")

        confirm = MagicMock()
        mock_count = AsyncMock(return_value=5)
        mock_delete = AsyncMock(return_value=5)

        monkeypatch.setattr("scripts.llm.cli.click.confirm", confirm)
        monkeypatch.setattr("scripts.llm.cli._count_evaluations", mock_count)
        monkeypatch.setattr("scripts.llm.cli._delete_evaluations", mock_delete)

        await _clear_main(project="snapcraft", yes=False)

        mock_count.assert_awaited_once()
        confirm.assert_called_once()
        mock_delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_clear_main_skips_delete_when_no_evaluations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DASHBOARD_URL", "http://testserver")
        monkeypatch.setenv("EVAL_API_TOKEN", "test-token")

        confirm = MagicMock()
        mock_count = AsyncMock(return_value=0)
        mock_delete = AsyncMock()

        monkeypatch.setattr("scripts.llm.cli.click.confirm", confirm)
        monkeypatch.setattr("scripts.llm.cli._count_evaluations", mock_count)
        monkeypatch.setattr("scripts.llm.cli._delete_evaluations", mock_delete)

        await _clear_main(project="", yes=False)

        mock_count.assert_awaited_once()
        confirm.assert_not_called()
        mock_delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_count_and_delete_evaluations_http_requests(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_get_resp = MagicMock()
        mock_get_resp.json.return_value = {"count": 42}
        mock_get_resp.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_get_resp

        headers = {"Authorization": "Bearer tok"}
        count = await _count_evaluations(mock_client, "rockcraft", headers)
        assert count == 42
        mock_client.get.assert_awaited_once_with(
            "/api/eval/clear",
            params={"project": "rockcraft"},
            headers=headers,
        )

        mock_del_resp = MagicMock()
        mock_del_resp.json.return_value = {"deleted": 42}
        mock_del_resp.raise_for_status = MagicMock()
        mock_client.request.return_value = mock_del_resp

        deleted = await _delete_evaluations(mock_client, "rockcraft", headers)
        assert deleted == 42
        mock_client.request.assert_awaited_once_with(
            "DELETE",
            "/api/eval/clear",
            params={"project": "rockcraft"},
            headers=headers,
        )


class TestEvaluateCliCommand:
    def test_copilot_acp_missing_model_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When --llm-backend copilot-acp is specified without COPILOT_ACP_MODEL, exit with error."""
        monkeypatch.delenv("COPILOT_ACP_MODEL", raising=False)
        monkeypatch.setenv("DASHBOARD_URL", "http://localhost:8000")
        monkeypatch.setenv("EVAL_API_TOKEN", "token")
        monkeypatch.setenv("LLM_CONFIG_ERROR_DELAY_SECONDS", "0")

        runner = CliRunner()
        result = runner.invoke(cli, ["evaluate", "--llm-backend", "copilot-acp"])
        assert result.exit_code != 0
        assert (
            "Missing required environment variable: COPILOT_ACP_MODEL" in result.output
        )
