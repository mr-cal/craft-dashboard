"""Tests for the HTTP-backed evaluate CLI."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner
from scripts.llm.cli import cli


def test_help_lists_http_evaluate_options() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["evaluate", "--help"])

    assert result.exit_code == 0
    assert "--server" in result.output
    assert "--ca-cert" in result.output
    assert "--llm-backend" in result.output
    assert "--interval" in result.output
    assert "--concurrency" in result.output
    assert "continuous" in result.output.lower()


def test_evaluate_uses_http_worker_with_local_backend(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
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
            "--server",
            "http://localhost:8000",
            "--token",
            "test-token",
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
    assert run_loop.call_args.kwargs["llm_backend"] == "local"
    assert run_loop.call_args.kwargs["concurrency"] == 4
    assert run_loop.call_args.kwargs["poll_interval"] == 15


def test_evaluate_requires_openrouter_api_key_even_for_local_backend(
    monkeypatch,
) -> None:
    runner = CliRunner()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "local-model")
    monkeypatch.setattr("scripts.llm.cli.run_evaluate_loop", AsyncMock())

    def _capture_run(coro):
        coro.close()

    monkeypatch.setattr("scripts.llm.cli.asyncio.run", _capture_run)

    result = runner.invoke(
        cli,
        [
            "evaluate",
            "--server",
            "http://localhost:8000",
            "--token",
            "test-token",
            "--llm-backend",
            "local",
        ],
    )

    assert result.exit_code != 0
    assert "OPENROUTER_API_KEY" in result.output


def test_evaluate_requires_openrouter_summary_model_for_openrouter_backend(
    monkeypatch,
) -> None:
    """OPENROUTER_MODEL_SUMMARY must be set explicitly; no silent fallback."""
    runner = CliRunner()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
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
            "--server",
            "http://localhost:8000",
            "--token",
            "test-token",
            "--llm-backend",
            "openrouter",
        ],
    )

    assert result.exit_code != 0
    assert "OPENROUTER_MODEL_SUMMARY" in result.output


def test_evaluate_uses_configured_openrouter_model(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
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
            "--server",
            "http://localhost:8000",
            "--token",
            "test-token",
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
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
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
            "--server",
            "http://localhost:8000",
            "--token",
            "test-token",
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
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "local-model")
    nonexistent = tmp_path / "missing.pem"

    result = runner.invoke(
        cli,
        [
            "evaluate",
            "--server",
            "http://localhost:8000",
            "--token",
            "test-token",
            "--llm-backend",
            "local",
            "--ca-cert",
            str(nonexistent),
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
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    nonexistent = tmp_path / "missing_server.pem"

    result = runner.invoke(
        cli,
        [
            "evaluate",
            "--server",
            "http://localhost:8000",
            "--token",
            "test-token",
            "--server-ca-cert",
            str(nonexistent),
        ],
    )

    assert result.exit_code != 0
    assert "Server CA certificate file not found" in result.output
    assert str(nonexistent) in result.output
