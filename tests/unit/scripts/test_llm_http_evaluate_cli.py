"""Tests for the HTTP-backed evaluate CLI."""

from __future__ import annotations

from unittest.mock import AsyncMock

import click
import pytest
from click.testing import CliRunner
from scripts.llm.cli import _handle_fatal_config_error, cli


def test_help_lists_http_evaluate_options() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["evaluate", "--help"])

    assert result.exit_code == 0
    assert "--server" not in result.output
    assert "--token" not in result.output
    assert "--ca-cert" not in result.output
    assert "--server-ca-cert" not in result.output
    assert "--llm-backend" in result.output
    assert "--interval" in result.output
    assert "--concurrency" in result.output
    assert "--log" in result.output
    assert "continuous" in result.output.lower()


def test_evaluate_uses_http_worker_with_local_backend(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setenv("EVAL_CLIENT_SERVER", "http://localhost:8000")
    monkeypatch.setenv("EVAL_API_TOKEN", "test-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_API_KEY_EMBEDDING", "test-embedding-key")
    monkeypatch.setenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "local-model")
    monkeypatch.delenv("LOCAL_LLM_CA_CERT", raising=False)
    run_loop = AsyncMock()
    monkeypatch.setattr("scripts.llm.cli.run_evaluate_loop", run_loop)

    def _capture_run(coro):
        coro.close()

    monkeypatch.setattr("scripts.llm.cli.asyncio.run", _capture_run)

    result = runner.invoke(
        cli,
        [
            "evaluate",
            "--llm-backend",
            "local",
            "--concurrency",
            "4",
            "--interval",
            "15",
        ],
    )

    assert result.exit_code == 0
    run_loop.assert_called_once()
    assert run_loop.call_args.kwargs["server"] == "http://localhost:8000"
    assert run_loop.call_args.kwargs["token"] == "test-token"
    assert run_loop.call_args.kwargs["llm_backend"] == "local"
    assert run_loop.call_args.kwargs["concurrency"] == 4
    assert run_loop.call_args.kwargs["log"] is False


def test_evaluate_passes_log_flag(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setenv("EVAL_CLIENT_SERVER", "http://localhost:8000")
    monkeypatch.setenv("EVAL_API_TOKEN", "test-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_API_KEY_EMBEDDING", "test-embedding-key")
    monkeypatch.setenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "local-model")
    monkeypatch.delenv("LOCAL_LLM_CA_CERT", raising=False)
    run_loop = AsyncMock()
    monkeypatch.setattr("scripts.llm.cli.run_evaluate_loop", run_loop)

    def _capture_run(coro):
        coro.close()

    monkeypatch.setattr("scripts.llm.cli.asyncio.run", _capture_run)

    result = runner.invoke(
        cli,
        [
            "evaluate",
            "--llm-backend",
            "local",
            "--log",
        ],
    )

    assert result.exit_code == 0
    run_loop.assert_called_once()
    assert run_loop.call_args.kwargs["log"] is True
    assert run_loop.call_args.kwargs["poll_interval"] == 30


def test_evaluate_local_backend_does_not_require_embedding_key(
    monkeypatch,
) -> None:
    runner = CliRunner()
    monkeypatch.setenv("EVAL_CLIENT_SERVER", "http://localhost:8000")
    monkeypatch.setenv("EVAL_API_TOKEN", "test-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.delenv("OPENROUTER_API_KEY_EMBEDDING", raising=False)
    monkeypatch.setenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "local-model")
    monkeypatch.delenv("LOCAL_LLM_CA_CERT", raising=False)
    run_loop = AsyncMock()
    monkeypatch.setattr("scripts.llm.cli.run_evaluate_loop", run_loop)

    def _capture_run(coro):
        coro.close()

    monkeypatch.setattr("scripts.llm.cli.asyncio.run", _capture_run)

    result = runner.invoke(
        cli,
        [
            "evaluate",
            "--llm-backend",
            "local",
        ],
    )

    assert result.exit_code == 0
    run_loop.assert_called_once()


def test_evaluate_requires_openrouter_summary_model_for_openrouter_backend(
    monkeypatch,
) -> None:
    """OPENROUTER_MODEL_SUMMARY must be set explicitly; no silent fallback."""
    runner = CliRunner()
    monkeypatch.setenv("EVAL_CLIENT_SERVER", "http://localhost:8000")
    monkeypatch.setenv("EVAL_API_TOKEN", "test-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_API_KEY_EMBEDDING", "test-embedding-key")
    monkeypatch.delenv("OPENROUTER_MODEL_SUMMARY", raising=False)
    monkeypatch.setenv("OPENROUTER_MODEL_SCORING", "qwen/qwen3.8-27b")
    monkeypatch.setattr("scripts.llm.cli.run_evaluate_loop", AsyncMock())

    def _capture_run(coro):
        coro.close()

    monkeypatch.setattr("scripts.llm.cli.asyncio.run", _capture_run)

    result = runner.invoke(
        cli,
        [
            "evaluate",
            "--llm-backend",
            "openrouter",
        ],
    )

    assert result.exit_code != 0
    assert "OPENROUTER_MODEL_SUMMARY" in result.output


def test_evaluate_uses_configured_openrouter_model(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setenv("EVAL_CLIENT_SERVER", "http://localhost:8000")
    monkeypatch.setenv("EVAL_API_TOKEN", "test-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_API_KEY_EMBEDDING", "test-embedding-key")
    monkeypatch.setenv("OPENROUTER_MODEL_SUMMARY", "qwen/qwen3.8-27b")
    monkeypatch.setenv("OPENROUTER_MODEL_SCORING", "qwen/qwen3.8-27b")
    run_loop = AsyncMock()
    monkeypatch.setattr("scripts.llm.cli.run_evaluate_loop", run_loop)

    def _capture_run(coro):
        coro.close()

    monkeypatch.setattr("scripts.llm.cli.asyncio.run", _capture_run)

    result = runner.invoke(
        cli,
        [
            "evaluate",
            "--llm-backend",
            "openrouter",
        ],
    )

    assert result.exit_code == 0
    run_loop.assert_called_once()
    assert run_loop.call_args.kwargs["model_summary"] == "qwen/qwen3.8-27b"
    assert run_loop.call_args.kwargs["model_scoring"] == "qwen/qwen3.8-27b"


@pytest.mark.parametrize("option_name", ["--limit", "--max-evaluations"])
def test_evaluate_accepts_limit_option_spellings(monkeypatch, option_name: str) -> None:
    runner = CliRunner()
    monkeypatch.setenv("EVAL_CLIENT_SERVER", "http://localhost:8000")
    monkeypatch.setenv("EVAL_API_TOKEN", "test-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_API_KEY_EMBEDDING", "test-embedding-key")
    monkeypatch.setenv("OPENROUTER_MODEL_SUMMARY", "qwen/qwen3.8-27b")
    monkeypatch.setenv("OPENROUTER_MODEL_SCORING", "qwen/qwen3.8-27b")
    run_loop = AsyncMock()
    monkeypatch.setattr("scripts.llm.cli.run_evaluate_loop", run_loop)

    def _capture_run(coro):
        coro.close()

    monkeypatch.setattr("scripts.llm.cli.asyncio.run", _capture_run)

    result = runner.invoke(
        cli,
        [
            "evaluate",
            option_name,
            "20",
        ],
    )

    assert result.exit_code == 0
    run_loop.assert_called_once()
    assert run_loop.call_args.kwargs["limit"] == 20


def test_evaluate_missing_ca_cert_raises_usage_error(monkeypatch, tmp_path) -> None:
    """Missing local LLM CA certificate raises a clear UsageError."""
    runner = CliRunner()
    monkeypatch.setenv("EVAL_CLIENT_SERVER", "http://localhost:8000")
    monkeypatch.setenv("EVAL_API_TOKEN", "test-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_API_KEY_EMBEDDING", "test-embedding-key")
    monkeypatch.setenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "local-model")
    nonexistent = tmp_path / "missing.pem"
    monkeypatch.setenv("LOCAL_LLM_CA_CERT", str(nonexistent))

    result = runner.invoke(
        cli,
        [
            "evaluate",
            "--llm-backend",
            "local",
        ],
    )

    assert result.exit_code != 0
    assert "CA certificate file not found" in result.output
    assert str(nonexistent) in result.output


def test_evaluate_missing_server_ca_cert_raises_usage_error(
    monkeypatch, tmp_path
) -> None:
    """Missing server CA certificate raises a clear UsageError."""
    runner = CliRunner()
    monkeypatch.setenv("EVAL_CLIENT_SERVER", "http://localhost:8000")
    monkeypatch.setenv("EVAL_API_TOKEN", "test-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_API_KEY_EMBEDDING", "test-embedding-key")
    nonexistent = tmp_path / "missing_server.pem"
    monkeypatch.setenv("EVAL_CLIENT_SERVER_CA_CERT", str(nonexistent))

    result = runner.invoke(
        cli,
        [
            "evaluate",
        ],
    )

    assert result.exit_code != 0
    assert "Server CA certificate file not found" in result.output
    assert str(nonexistent) in result.output


def test_evaluate_missing_server_or_token_raises_usage_error(monkeypatch) -> None:
    """Missing EVAL_CLIENT_SERVER or EVAL_API_TOKEN raises a clear UsageError."""
    runner = CliRunner()
    monkeypatch.delenv("EVAL_CLIENT_SERVER", raising=False)
    monkeypatch.delenv("EVAL_API_TOKEN", raising=False)

    result = runner.invoke(cli, ["evaluate"])
    assert result.exit_code != 0
    assert (
        "Missing required environment variable(s): EVAL_CLIENT_SERVER, EVAL_API_TOKEN"
        in result.output
    )


def test_handle_fatal_config_error_sleeps_when_delay_positive(monkeypatch) -> None:
    """_handle_fatal_config_error sleeps before raising UsageError to prevent tight crash loops."""
    slept = []
    monkeypatch.setattr("scripts.llm.cli.time.sleep", slept.append)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("LLM_CONFIG_ERROR_DELAY_SECONDS", "10")

    with pytest.raises(click.UsageError, match="test error"):
        _handle_fatal_config_error("test error")

    assert slept == [10.0]


def test_handle_fatal_config_error_no_sleep_when_delay_zero(monkeypatch) -> None:
    """_handle_fatal_config_error skips sleep when LLM_CONFIG_ERROR_DELAY_SECONDS is 0."""
    slept = []
    monkeypatch.setattr("scripts.llm.cli.time.sleep", slept.append)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("LLM_CONFIG_ERROR_DELAY_SECONDS", "0")

    with pytest.raises(click.UsageError, match="test error"):
        _handle_fatal_config_error("test error")

    assert slept == []


def test_evaluate_slow_eval_options_and_concurrency(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setenv("EVAL_CLIENT_SERVER", "http://localhost:8000")
    monkeypatch.setenv("EVAL_API_TOKEN", "test-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_API_KEY_EMBEDDING", "test-embedding-key")
    monkeypatch.setenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "local-model")
    monkeypatch.delenv("LOCAL_LLM_CA_CERT", raising=False)
    run_loop = AsyncMock()
    monkeypatch.setattr("scripts.llm.cli.run_evaluate_loop", run_loop)

    def _capture_run(coro):
        coro.close()

    monkeypatch.setattr("scripts.llm.cli.asyncio.run", _capture_run)

    result = runner.invoke(
        cli,
        [
            "evaluate",
            "--llm-backend",
            "local",
            "--slow-eval",
            "--min-delay",
            "10.5",
            "--max-delay",
            "30.0",
            "--concurrency",
            "4",
        ],
    )

    assert result.exit_code == 0
    run_loop.assert_called_once()
    assert run_loop.call_args.kwargs["slow_eval"] is True
    assert run_loop.call_args.kwargs["min_delay"] == 10.5
    assert run_loop.call_args.kwargs["max_delay"] == 30.0
    assert run_loop.call_args.kwargs["tool_delay"] == 7.0
    # Concurrency forced to 1 with --slow-eval
    assert run_loop.call_args.kwargs["concurrency"] == 1


def test_evaluate_slow_eval_custom_tool_delay(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setenv("EVAL_CLIENT_SERVER", "http://localhost:8000")
    monkeypatch.setenv("EVAL_API_TOKEN", "test-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_API_KEY_EMBEDDING", "test-embedding-key")
    monkeypatch.setenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "local-model")
    monkeypatch.delenv("LOCAL_LLM_CA_CERT", raising=False)
    run_loop = AsyncMock()
    monkeypatch.setattr("scripts.llm.cli.run_evaluate_loop", run_loop)

    def _capture_run(coro):
        coro.close()

    monkeypatch.setattr("scripts.llm.cli.asyncio.run", _capture_run)

    result = runner.invoke(
        cli,
        [
            "evaluate",
            "--llm-backend",
            "local",
            "--slow-eval",
            "--tool-delay",
            "12.5",
        ],
    )

    assert result.exit_code == 0
    run_loop.assert_called_once()
    assert run_loop.call_args.kwargs["tool_delay"] == 12.5


def test_evaluate_tool_delay_defaults_to_zero_without_slow_eval(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setenv("EVAL_CLIENT_SERVER", "http://localhost:8000")
    monkeypatch.setenv("EVAL_API_TOKEN", "test-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_API_KEY_EMBEDDING", "test-embedding-key")
    monkeypatch.setenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "local-model")
    monkeypatch.delenv("LOCAL_LLM_CA_CERT", raising=False)
    run_loop = AsyncMock()
    monkeypatch.setattr("scripts.llm.cli.run_evaluate_loop", run_loop)

    def _capture_run(coro):
        coro.close()

    monkeypatch.setattr("scripts.llm.cli.asyncio.run", _capture_run)

    result = runner.invoke(
        cli,
        [
            "evaluate",
            "--llm-backend",
            "local",
        ],
    )

    assert result.exit_code == 0
    run_loop.assert_called_once()
    assert run_loop.call_args.kwargs["tool_delay"] == 0.0


def test_evaluate_slow_eval_invalid_delay_range(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setenv("EVAL_CLIENT_SERVER", "http://localhost:8000")
    monkeypatch.setenv("EVAL_API_TOKEN", "test-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("OPENROUTER_API_KEY_EMBEDDING", "test-embedding-key")
    monkeypatch.setenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "local-model")

    result = runner.invoke(
        cli,
        [
            "evaluate",
            "--llm-backend",
            "local",
            "--slow-eval",
            "--min-delay",
            "60",
            "--max-delay",
            "20",
        ],
    )

    assert result.exit_code != 0
    assert "--min-delay cannot be greater than --max-delay" in result.output
