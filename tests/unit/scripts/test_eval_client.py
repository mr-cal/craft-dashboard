"""Tests for scripts.eval_client."""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any, Self
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from scripts import eval_client

SAMPLE_ISSUE = {
    "issue_id": 42,
    "project_name": "snapcraft",
    "external_id": "100",
    "title": "Test issue",
    "state": "open",
    "issue_type": "issue",
    "body": "Test body content here for the issue",
    "comments": [],
    "labels": ["bug"],
    "author": "user1",
    "author_association": "CONTRIBUTOR",
    "created_at": "2026-01-01T00:00:00+00:00",
    "updated_at": "2026-05-01T00:00:00+00:00",
    "current_hash": "",
    "maintainers": ["alice"],
}

# evaluate returns EvaluationResult TypedDict
_SAMPLE_EVALUATE_RESULT = {
    "summary": "This is a test summary for the issue evaluation.",
    "scores": {
        "staleness": 10,
        "complexity": 30,
        "support_request": 0,
        "readiness": 50,
        "confidence": 80,
    },
    "suggested_action": "needs_triage",
    "suggested_action_reason": "Needs investigation by maintainer",
    "tokens_used": 500,
    "prompt_tokens": 300,
    "completion_tokens": 200,
    "issue_data_hash": "unit_test_eval_hash",
}

# Expected token counts from the evaluate result
_EXPECTED_PROMPT_TOKENS = 300
_EXPECTED_COMPLETION_TOKENS = 200
_EXPECTED_TOKENS_USED = 500

DEFAULT_KWARGS = {
    "server": "http://localhost:8000",
    "token": "test-token",
    "model": "test-model",
    "llm_url": "http://localhost:11434/v1",
    "llm_api_key": "",
    "ca_cert": "",
    "poll_interval": 1,
    "limit": 1,
    "project": "",
    "open_only": True,
    "force": False,
    "incomplete": False,
    "stale_days": 0,
    "server_ca_cert": "",
    "verbose": False,
    "embed_model": "",
}


class MockAsyncClient:
    def __init__(
        self,
        *,
        get_responses: list[httpx.Response],
        post_responses: list[httpx.Response] | None = None,
    ) -> None:
        self.get = AsyncMock(side_effect=get_responses)
        self.post = AsyncMock(side_effect=post_responses or [])

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb


def _make_issue(**updates: Any) -> dict[str, Any]:
    issue = deepcopy(SAMPLE_ISSUE)
    issue.update(updates)
    return issue


@pytest.fixture(autouse=True)
def _reset_shutdown_state() -> None:
    eval_client.shutdown_state["requested"] = False
    eval_client.paused_state["paused"] = False


_STATUS_RESPONSE = httpx.Response(
    status_code=200,
    json={
        "pending": 10,
        "locked": 0,
        "evaluated_today": 5,
        "total_evaluated": 5,
        "total_open": 15,
    },
)

_DUMMY_REQUEST = httpx.Request("GET", "http://localhost:8000/api/eval/next")


def _response(status_code: int, **kwargs) -> httpx.Response:
    """Create an httpx.Response with a request attached (required for response.url)."""
    r = httpx.Response(status_code=status_code, **kwargs)
    r.request = _DUMMY_REQUEST
    return r


def _with_status(*responses: httpx.Response) -> list[httpx.Response]:
    """Prepend a status response to a list of responses (startup call)."""
    return [_STATUS_RESPONSE, *responses]


@pytest.fixture
def patched_runtime(monkeypatch):
    llm_client = MagicMock()
    llm_client.close = AsyncMock()
    evaluator = MagicMock()
    evaluator.evaluate = AsyncMock(return_value=_SAMPLE_EVALUATE_RESULT)
    sleep_mock = AsyncMock()

    # Mock progress bar to avoid rich terminal output in tests
    mock_progress = MagicMock()
    mock_progress.__enter__ = MagicMock(return_value=mock_progress)
    mock_progress.__exit__ = MagicMock(return_value=False)
    mock_progress.add_task = MagicMock(return_value=0)

    # Capture console.print calls (used for per-issue completion lines)
    console_prints: list[str] = []
    mock_console = MagicMock()
    mock_console.print = lambda *args, **kwargs: console_prints.append(str(args[0]))
    mock_progress.console = mock_console

    # Mock timing history to avoid writing to ~/.craft-dashboard/
    mock_timing = MagicMock()
    mock_timing.add = MagicMock()
    mock_timing.eta = MagicMock(return_value="?")

    monkeypatch.setattr(
        eval_client, "LocalLLMClient", MagicMock(return_value=llm_client)
    )
    monkeypatch.setattr(
        eval_client, "IssueEvaluator", MagicMock(return_value=evaluator)
    )
    monkeypatch.setattr(eval_client, "_sleep_until_next_poll", sleep_mock)
    monkeypatch.setattr(eval_client.signal, "signal", MagicMock())
    monkeypatch.setattr(eval_client, "_start_keyboard_monitor", MagicMock())
    monkeypatch.setattr(
        eval_client, "_make_progress", MagicMock(return_value=mock_progress)
    )
    monkeypatch.setattr(
        eval_client, "TimingHistory", MagicMock(return_value=mock_timing)
    )
    # Suppress rich console output from _setup_logging in tests
    monkeypatch.setattr(eval_client, "_setup_logging", MagicMock())

    return {
        "llm_client": llm_client,
        "evaluator": evaluator,
        "sleep": sleep_mock,
        "progress": mock_progress,
        "timing": mock_timing,
        "console_prints": console_prints,
    }


def _patch_http_client(
    monkeypatch,
    *,
    get_responses: list[httpx.Response],
    post_responses: list[httpx.Response] | None = None,
) -> MockAsyncClient:
    http_client = MockAsyncClient(
        get_responses=get_responses,
        post_responses=post_responses,
    )
    monkeypatch.setattr(
        eval_client.httpx,
        "AsyncClient",
        MagicMock(return_value=http_client),
    )
    return http_client


@pytest.mark.asyncio
async def test_run_eval_loop_processes_issue_and_stops_at_limit(
    monkeypatch, patched_runtime
) -> None:
    http_client = _patch_http_client(
        monkeypatch,
        get_responses=_with_status(httpx.Response(status_code=200, json=_make_issue())),
        post_responses=[httpx.Response(status_code=200)],
    )

    await eval_client.run_eval_loop(**DEFAULT_KWARGS)

    patched_runtime["evaluator"].evaluate.assert_awaited_once()
    http_client.post.assert_awaited_once()
    posted = http_client.post.await_args.kwargs["json"]
    assert posted["issue_id"] == 42
    assert posted["summary"] == _SAMPLE_EVALUATE_RESULT["summary"]
    assert posted["suggested_action"] == "needs_triage"
    assert posted["prompt_tokens"] == _EXPECTED_PROMPT_TOKENS
    assert posted["completion_tokens"] == _EXPECTED_COMPLETION_TOKENS
    assert posted["tokens_used"] == _EXPECTED_TOKENS_USED
    assert posted["model_used"] == "test-model"
    assert posted["llm_backend"] == "local"
    assert posted["summary_embedding"] is None
    patched_runtime["sleep"].assert_not_awaited()
    patched_runtime["llm_client"].close.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_eval_loop_sleeps_without_evaluating_when_no_work(
    monkeypatch, patched_runtime
) -> None:
    async def request_shutdown(_seconds: int) -> None:
        eval_client.shutdown_state["requested"] = True

    patched_runtime["sleep"].side_effect = request_shutdown
    http_client = _patch_http_client(
        monkeypatch,
        get_responses=_with_status(httpx.Response(status_code=204)),
    )

    await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 0})

    assert http_client.get.await_count == 2  # status + next
    patched_runtime["evaluator"].evaluate.assert_not_awaited()
    http_client.post.assert_not_awaited()
    patched_runtime["sleep"].assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_run_eval_loop_logs_warning_and_continues_on_submit_conflict(
    monkeypatch, patched_runtime, caplog
) -> None:
    async def request_shutdown(_seconds: int) -> None:
        eval_client.shutdown_state["requested"] = True

    patched_runtime["sleep"].side_effect = request_shutdown
    http_client = _patch_http_client(
        monkeypatch,
        get_responses=_with_status(
            httpx.Response(status_code=200, json=_make_issue()),
            httpx.Response(status_code=204),
        ),
        post_responses=[httpx.Response(status_code=409, text="conflict")],
    )

    with caplog.at_level(logging.WARNING):
        await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 0})

    patched_runtime["evaluator"].evaluate.assert_awaited_once()
    http_client.post.assert_awaited_once()
    patched_runtime["sleep"].assert_awaited_once_with(1)
    assert "content changed during evaluation" in caplog.text


@pytest.mark.asyncio
async def test_run_eval_loop_stops_after_reaching_limit(
    monkeypatch, patched_runtime, caplog
) -> None:
    patched_runtime["evaluator"].evaluate = AsyncMock(
        side_effect=[_SAMPLE_EVALUATE_RESULT, _SAMPLE_EVALUATE_RESULT]
    )
    http_client = _patch_http_client(
        monkeypatch,
        get_responses=_with_status(
            httpx.Response(
                status_code=200, json=_make_issue(issue_id=1, external_id="1")
            ),
            httpx.Response(
                status_code=200, json=_make_issue(issue_id=2, external_id="2")
            ),
        ),
        post_responses=[
            httpx.Response(status_code=200),
            httpx.Response(status_code=200),
        ],
    )

    with caplog.at_level(logging.INFO):
        await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 2})

    assert patched_runtime["evaluator"].evaluate.await_count == 2
    assert http_client.post.await_count == 2
    assert "Done: evaluated 2 issues" in caplog.text
    assert (
        f"Run total: 2 issues, {2 * _EXPECTED_PROMPT_TOKENS} in"
        f" / {2 * _EXPECTED_COMPLETION_TOKENS} out tokens"
        f" ({2 * _EXPECTED_TOKENS_USED} total)"
    ) in caplog.text


@pytest.mark.asyncio
async def test_run_eval_loop_sleeps_and_retries_after_server_error(
    monkeypatch, patched_runtime
) -> None:
    http_client = _patch_http_client(
        monkeypatch,
        get_responses=_with_status(
            _response(500, text="boom"),
            _response(200, json=_make_issue()),
        ),
        post_responses=[_response(200)],
    )

    await eval_client.run_eval_loop(**DEFAULT_KWARGS)

    assert http_client.get.await_count == 3  # status + 500 + issue
    patched_runtime["sleep"].assert_awaited_once_with(1)
    patched_runtime["evaluator"].evaluate.assert_awaited_once()
    http_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_eval_loop_continues_after_evaluate_exception(
    monkeypatch, patched_runtime, caplog
) -> None:
    """If evaluate raises, that issue is skipped and the loop continues."""

    async def request_shutdown(_seconds: int) -> None:
        eval_client.shutdown_state["requested"] = True

    patched_runtime["evaluator"].evaluate = AsyncMock(
        side_effect=RuntimeError("LLM timeout")
    )
    patched_runtime["sleep"].side_effect = request_shutdown

    http_client = _patch_http_client(
        monkeypatch,
        get_responses=_with_status(
            httpx.Response(status_code=200, json=_make_issue()),
            httpx.Response(status_code=204),
        ),
    )

    with caplog.at_level(logging.ERROR):
        await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 0})

    http_client.post.assert_not_awaited()
    assert "evaluation failed" in caplog.text


@pytest.mark.asyncio
async def test_run_eval_loop_prints_per_issue_completion_line(
    monkeypatch, patched_runtime
) -> None:
    """Each evaluated issue produces exactly one completion line via console.print."""
    _patch_http_client(
        monkeypatch,
        get_responses=_with_status(httpx.Response(status_code=200, json=_make_issue())),
        post_responses=[httpx.Response(status_code=200)],
    )

    await eval_client.run_eval_loop(**DEFAULT_KWARGS)

    printed = " ".join(patched_runtime["console_prints"])
    assert "300 in / 200 out" in printed
    assert "needs_triage" in printed
    assert "eval " in printed
    assert "embed " in printed


@pytest.mark.asyncio
async def test_run_eval_loop_run_total_logged_after_multiple_issues(
    monkeypatch, patched_runtime, caplog
) -> None:
    """Run total is logged to caplog (not console.print)."""
    _patch_http_client(
        monkeypatch,
        get_responses=_with_status(httpx.Response(status_code=200, json=_make_issue())),
        post_responses=[httpx.Response(status_code=200)],
    )

    with caplog.at_level(logging.INFO):
        await eval_client.run_eval_loop(**DEFAULT_KWARGS)

    assert "Run total: 1 issues, 300 in / 200 out tokens (500 total)" in caplog.text


@pytest.mark.asyncio
async def test_embedding_client_created_once_for_multiple_issues(
    monkeypatch, patched_runtime
) -> None:
    """EmbeddingClient is created once before the loop, not per-issue."""
    mock_embed_client = MagicMock()
    mock_embed_client.close = AsyncMock()
    mock_embed_client.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    mock_embedding_class = MagicMock(return_value=mock_embed_client)
    monkeypatch.setattr(eval_client, "EmbeddingClient", mock_embedding_class)

    patched_runtime["evaluator"].evaluate = AsyncMock(
        side_effect=[_SAMPLE_EVALUATE_RESULT, _SAMPLE_EVALUATE_RESULT]
    )

    _patch_http_client(
        monkeypatch,
        get_responses=_with_status(
            httpx.Response(
                status_code=200, json=_make_issue(issue_id=1, external_id="1")
            ),
            httpx.Response(
                status_code=200, json=_make_issue(issue_id=2, external_id="2")
            ),
        ),
        post_responses=[
            httpx.Response(status_code=200),
            httpx.Response(status_code=200),
        ],
    )

    await eval_client.run_eval_loop(
        **{**DEFAULT_KWARGS, "limit": 2, "embed_model": "nomic-embed-text"}
    )

    assert mock_embedding_class.call_count == 1  # constructed once, not per-issue
    mock_embed_client.close.assert_awaited_once()  # closed in finally


@pytest.mark.asyncio
async def test_evaluate_then_embed_run_in_serial(monkeypatch, patched_runtime) -> None:
    """Embed runs after evaluate completes (serial flow)."""
    call_order: list[str] = []

    async def fake_evaluate(**_kwargs):
        call_order.append("evaluate")
        return _SAMPLE_EVALUATE_RESULT

    mock_embed_client = MagicMock()

    async def fake_embed(text):
        call_order.append("embed")
        return [0.1, 0.2, 0.3]

    mock_embed_client.embed = fake_embed
    mock_embed_client.close = AsyncMock()
    monkeypatch.setattr(
        eval_client, "EmbeddingClient", MagicMock(return_value=mock_embed_client)
    )

    patched_runtime["evaluator"].evaluate = fake_evaluate

    _patch_http_client(
        monkeypatch,
        get_responses=_with_status(httpx.Response(status_code=200, json=_make_issue())),
        post_responses=[httpx.Response(status_code=200)],
    )

    await eval_client.run_eval_loop(
        **{**DEFAULT_KWARGS, "embed_model": "nomic-embed-text"}
    )

    assert call_order == ["evaluate", "embed"]


@pytest.mark.asyncio
async def test_embedding_failure_does_not_abort_submission(
    monkeypatch, patched_runtime, caplog
) -> None:
    """If embed raises, submission still proceeds with summary_embedding=None."""
    mock_embed_client = MagicMock()
    mock_embed_client.embed = AsyncMock(side_effect=RuntimeError("embed server down"))
    mock_embed_client.close = AsyncMock()
    monkeypatch.setattr(
        eval_client, "EmbeddingClient", MagicMock(return_value=mock_embed_client)
    )

    http_client = _patch_http_client(
        monkeypatch,
        get_responses=_with_status(httpx.Response(status_code=200, json=_make_issue())),
        post_responses=[httpx.Response(status_code=200)],
    )

    with caplog.at_level(logging.WARNING):
        await eval_client.run_eval_loop(
            **{**DEFAULT_KWARGS, "embed_model": "nomic-embed-text"}
        )

    http_client.post.assert_awaited_once()
    posted = http_client.post.await_args.kwargs["json"]
    assert posted["summary_embedding"] is None
    assert "embedding failed" in caplog.text
