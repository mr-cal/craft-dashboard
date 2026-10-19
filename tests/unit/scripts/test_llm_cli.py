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

    @pytest.mark.asyncio
    async def test_count_and_delete_with_fine_grained_filters(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_get_resp = MagicMock()
        mock_get_resp.json.return_value = {"count": 10}
        mock_get_resp.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_get_resp

        mock_del_resp = MagicMock()
        mock_del_resp.json.return_value = {"deleted": 10}
        mock_del_resp.raise_for_status = MagicMock()
        mock_client.request.return_value = mock_del_resp

        count = await _count_evaluations(
            mock_client,
            project="rockcraft",
            headers={"Authorization": "Bearer tok"},
            issue="101",
            evaluated_by="worker-test",
            stale_days=14,
            incomplete=True,
        )
        assert count == 10
        mock_client.get.assert_awaited_once_with(
            "/api/eval/clear",
            params={
                "project": "rockcraft",
                "issue": "101",
                "evaluated_by": "worker-test",
                "stale_days": 14,
                "incomplete": True,
            },
            headers={"Authorization": "Bearer tok"},
        )

        deleted = await _delete_evaluations(
            mock_client,
            project="rockcraft",
            headers={"Authorization": "Bearer tok"},
            issue="101",
            evaluated_by="worker-test",
            stale_days=14,
            incomplete=True,
        )
        assert deleted == 10
        mock_client.request.assert_awaited_once_with(
            "DELETE",
            "/api/eval/clear",
            params={
                "project": "rockcraft",
                "issue": "101",
                "evaluated_by": "worker-test",
                "stale_days": 14,
                "incomplete": True,
            },
            headers={"Authorization": "Bearer tok"},
        )


class TestStatusCliCommand:
    def test_status_missing_token_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EVAL_API_TOKEN", raising=False)
        monkeypatch.delenv("CRAFT_EVAL_TOKEN", raising=False)
        runner = CliRunner()
        result = runner.invoke(cli, ["status"])
        assert result.exit_code != 0
        assert "Token Auth:" in result.output
        assert "MISSING" in result.output

    def test_status_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRAFT_EVAL_TOKEN", "test-token")
        monkeypatch.setenv("CRAFT_DASHBOARD_URL", "http://testserver")

        mock_status_resp = MagicMock()
        mock_status_resp.status_code = 200
        mock_status_resp.json.return_value = {
            "authenticated_as": "test-bot",
            "permissions": ["eval:write"],
            "min_worker_version": "0.1.0",
            "pending": 5,
            "total_open": 50,
            "total_evaluated": 45,
            "evaluated_today": 2,
        }

        mock_projects_resp = MagicMock()
        mock_projects_resp.status_code = 200
        mock_projects_resp.json.return_value = {"projects": {"snapcraft": "canonical"}}

        async def _mock_get(url, **kwargs):
            if "status" in url:
                return mock_status_resp
            return mock_projects_resp

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_mock_get)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        monkeypatch.setattr(
            "scripts.llm.cli.httpx.AsyncClient", lambda **kw: mock_client
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "Authenticated as 'test-bot'" in result.output
        assert "Compatible" in result.output
        assert "All core checks passed" in result.output

    def test_status_upgrade_required_426(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRAFT_EVAL_TOKEN", "test-token")
        monkeypatch.setenv("CRAFT_DASHBOARD_URL", "http://testserver")

        mock_status_resp = MagicMock()
        mock_status_resp.status_code = 426
        mock_status_resp.headers = {"content-type": "application/json"}
        mock_status_resp.json.return_value = {
            "detail": "Worker version 0.1.0 is outdated. Minimum is 0.5.0.",
            "remedy": "Run 'uvx --upgrade craft-eval' to update.",
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_status_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        monkeypatch.setattr(
            "scripts.llm.cli.httpx.AsyncClient", lambda **kw: mock_client
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["status"])
        assert result.exit_code != 0
        assert "UPGRADE REQUIRED" in result.output
        assert "uvx --upgrade craft-eval" in result.output


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
