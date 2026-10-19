"""Unit tests for scripts/manage_eval_tokens.py CLI."""

from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner
from scripts.manage_eval_tokens import cli

from tests.factories import make_eval_token


def test_manage_tokens_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "create" in result.output
    assert "list" in result.output
    assert "revoke" in result.output


def test_create_token_cli() -> None:
    runner = CliRunner()
    mock_session = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.get_by_id = AsyncMock(return_value=None)
    mock_repo.create_token = AsyncMock()

    with (
        patch("scripts.manage_eval_tokens.EvalTokenRepository", return_value=mock_repo),
        patch(
            "scripts.manage_eval_tokens._get_session_factory",
            return_value=MagicMock(
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_session),
                    __aexit__=AsyncMock(),
                )
            ),
        ),
    ):
        result = runner.invoke(
            cli,
            ["create", "--id", "test-worker", "--name", "Test Worker", "--admin"],
        )
        assert result.exit_code == 0
        assert "Success: Created evaluation token" in result.output
        assert "test-worker" in result.output
        mock_repo.create_token.assert_awaited_once()
        assert mock_repo.create_token.call_args.kwargs["token_id"] == "test-worker"
        assert mock_repo.create_token.call_args.kwargs["permissions"] == [
            "eval:write",
            "eval:admin",
        ]


def test_list_tokens_cli() -> None:
    runner = CliRunner()
    token = make_eval_token(id="worker-1", name="Worker 1", is_active=True)
    mock_session = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.list_tokens = AsyncMock(return_value=[token])
    mock_repo.get_evaluation_counts_by_token = AsyncMock(return_value={"worker-1": 42})

    with (
        patch("scripts.manage_eval_tokens.EvalTokenRepository", return_value=mock_repo),
        patch(
            "scripts.manage_eval_tokens._get_session_factory",
            return_value=MagicMock(
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_session),
                    __aexit__=AsyncMock(),
                )
            ),
        ),
    ):
        result = runner.invoke(cli, ["list", "--stats"])
        assert result.exit_code == 0
        assert "worker-1" in result.output
        assert "42" in result.output


def test_revoke_token_cli() -> None:
    runner = CliRunner()
    mock_session = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.revoke_token = AsyncMock(return_value=True)

    with (
        patch("scripts.manage_eval_tokens.EvalTokenRepository", return_value=mock_repo),
        patch(
            "scripts.manage_eval_tokens._get_session_factory",
            return_value=MagicMock(
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_session),
                    __aexit__=AsyncMock(),
                )
            ),
        ),
        patch("scripts.manage_eval_tokens.evict_eval_token") as mock_evict,
    ):
        result = runner.invoke(cli, ["revoke", "--id", "worker-1"])
        assert result.exit_code == 0
        assert "Revoked: Token 'worker-1' is now inactive" in result.output
        mock_repo.revoke_token.assert_awaited_once_with("worker-1")
        mock_evict.assert_called_once_with("worker-1")
