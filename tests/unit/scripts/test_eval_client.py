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

SAMPLE_RESULT = {
    "summary": "This is a test summary for the issue evaluation.",
    "scores": {
        "staleness": 10,
        "complexity": 30,
        "support_request": 0,
        "readiness": 50,
    },
    "suggested_action": "needs_triage",
    "suggested_action_reason": "Needs investigation by maintainer",
    "tokens_used": 500,
    "prompt_tokens": 300,
    "completion_tokens": 200,
    "issue_data_hash": "abc123",
}

DEFAULT_KWARGS = {
    "server": "http://localhost:8000",
    "token": "test-token",
    "summary_model": "test-model",
    "evaluation_model": "test-model",
    "embedding_model": "",
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


def _with_status(*responses: httpx.Response) -> list[httpx.Response]:
    """Prepend a status response to a list of responses (startup call)."""
    return [_STATUS_RESPONSE, *responses]


@pytest.fixture
def patched_runtime(monkeypatch):
    llm_client = MagicMock()
    llm_client.close = AsyncMock()
    evaluator = MagicMock()
    evaluator.evaluate_issue = AsyncMock(return_value=SAMPLE_RESULT)
    sleep_mock = AsyncMock()

    monkeypatch.setattr(
        eval_client, "LocalLLMClient", MagicMock(return_value=llm_client)
    )
    monkeypatch.setattr(
        eval_client, "IssueEvaluator", MagicMock(return_value=evaluator)
    )
    monkeypatch.setattr(eval_client, "_sleep_until_next_poll", sleep_mock)
    monkeypatch.setattr(eval_client.signal, "signal", MagicMock())
    monkeypatch.setattr(eval_client, "_start_keyboard_monitor", MagicMock())

    return {
        "llm_client": llm_client,
        "evaluator": evaluator,
        "sleep": sleep_mock,
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

    patched_runtime["evaluator"].evaluate_issue.assert_awaited_once()
    http_client.post.assert_awaited_once()
    assert http_client.post.await_args.kwargs["json"] == {
        "issue_id": 42,
        "content_hash": "abc123",
        "summary": SAMPLE_RESULT["summary"],
        "scores": SAMPLE_RESULT["scores"],
        "suggested_action": SAMPLE_RESULT["suggested_action"],
        "suggested_action_reason": SAMPLE_RESULT["suggested_action_reason"],
        "tokens_used": SAMPLE_RESULT["tokens_used"],
        "prompt_tokens": SAMPLE_RESULT["prompt_tokens"],
        "completion_tokens": SAMPLE_RESULT["completion_tokens"],
        "model_used": "test-model",
        "llm_backend": "local",
        "embedding": None,
    }
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
    patched_runtime["evaluator"].evaluate_issue.assert_not_awaited()
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

    patched_runtime["evaluator"].evaluate_issue.assert_awaited_once()
    http_client.post.assert_awaited_once()
    patched_runtime["sleep"].assert_awaited_once_with(1)
    assert "content changed during evaluation" in caplog.text


@pytest.mark.asyncio
async def test_run_eval_loop_stops_after_reaching_limit(
    monkeypatch, patched_runtime, caplog
) -> None:
    first_result = deepcopy(SAMPLE_RESULT)
    second_result = deepcopy(SAMPLE_RESULT)
    second_result["issue_data_hash"] = "def456"
    patched_runtime["evaluator"].evaluate_issue.side_effect = [
        first_result,
        second_result,
    ]
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

    assert patched_runtime["evaluator"].evaluate_issue.await_count == 2
    assert http_client.post.await_count == 2
    assert "Done: evaluated 2 issues" in caplog.text


@pytest.mark.asyncio
async def test_run_eval_loop_sleeps_and_retries_after_server_error(
    monkeypatch, patched_runtime
) -> None:
    http_client = _patch_http_client(
        monkeypatch,
        get_responses=_with_status(
            httpx.Response(status_code=500, text="boom"),
            httpx.Response(status_code=200, json=_make_issue()),
        ),
        post_responses=[httpx.Response(status_code=200)],
    )

    await eval_client.run_eval_loop(**DEFAULT_KWARGS)

    assert http_client.get.await_count == 3  # status + 500 + issue
    patched_runtime["sleep"].assert_awaited_once_with(1)
    patched_runtime["evaluator"].evaluate_issue.assert_awaited_once()
    http_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_eval_loop_skips_submission_when_evaluator_returns_none(
    monkeypatch, patched_runtime, caplog
) -> None:
    async def request_shutdown(_seconds: int) -> None:
        eval_client.shutdown_state["requested"] = True

    patched_runtime["evaluator"].evaluate_issue.return_value = None
    patched_runtime["sleep"].side_effect = request_shutdown
    http_client = _patch_http_client(
        monkeypatch,
        get_responses=_with_status(
            httpx.Response(status_code=200, json=_make_issue()),
            httpx.Response(status_code=204),
        ),
    )

    with caplog.at_level(logging.INFO):
        await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 0})

    patched_runtime["evaluator"].evaluate_issue.assert_awaited_once()
    http_client.post.assert_not_awaited()
    patched_runtime["sleep"].assert_awaited_once_with(1)
    assert "content unchanged, skipped" in caplog.text
