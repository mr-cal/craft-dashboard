"""Tests for scripts.llm.eval_worker."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Self
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from scripts.llm import eval_worker

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

SAMPLE_EVALUATE_RESULT = {
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

DEFAULT_KWARGS = {
    "server": "http://localhost:8000",
    "token": "test-token",
    "model": "test-model",
    "llm_backend": "local",
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
    "embed_model": "openai/text-embedding-3-small",
    "openrouter_api_key": "test-openrouter-key",
    "concurrency": 1,
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
    eval_worker.shutdown_state["requested"] = False
    eval_worker.paused_state["paused"] = False


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


def _response(status_code: int, **kwargs: Any) -> httpx.Response:
    response = httpx.Response(status_code=status_code, **kwargs)
    response.request = _DUMMY_REQUEST
    return response


def _with_status(*responses: httpx.Response) -> list[httpx.Response]:
    return [_STATUS_RESPONSE, *responses]


@pytest.fixture
def patched_runtime(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    llm_client = MagicMock()
    llm_client.close = AsyncMock()
    evaluator = MagicMock()
    evaluator.evaluate = AsyncMock(return_value=SAMPLE_EVALUATE_RESULT)
    embed_client = MagicMock()
    embed_client.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    embed_client.close = AsyncMock()
    sleep_mock = AsyncMock()

    mock_progress = MagicMock()
    mock_progress.__enter__ = MagicMock(return_value=mock_progress)
    mock_progress.__exit__ = MagicMock(return_value=False)
    mock_progress.add_task = MagicMock(return_value=0)
    mock_progress.update = MagicMock()
    mock_console = MagicMock()
    mock_progress.console = mock_console

    monkeypatch.setattr(
        eval_worker,
        "create_llm_client_for_backend",
        MagicMock(return_value=llm_client),
    )
    monkeypatch.setattr(
        eval_worker,
        "IssueEvaluator",
        MagicMock(return_value=evaluator),
    )
    monkeypatch.setattr(
        eval_worker,
        "EmbeddingClient",
        MagicMock(return_value=embed_client),
    )
    monkeypatch.setattr(eval_worker, "_sleep_until_next_poll", sleep_mock)
    monkeypatch.setattr(eval_worker.signal, "signal", MagicMock())
    monkeypatch.setattr(eval_worker, "_start_keyboard_monitor", MagicMock())
    monkeypatch.setattr(
        eval_worker,
        "_make_progress",
        MagicMock(return_value=mock_progress),
    )
    monkeypatch.setattr(eval_worker, "_setup_logging", MagicMock())

    return {
        "llm_client": llm_client,
        "evaluator": evaluator,
        "embed_client": embed_client,
        "sleep": sleep_mock,
        "progress": mock_progress,
    }


def _patch_http_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    get_responses: list[httpx.Response],
    post_responses: list[httpx.Response] | None = None,
) -> MockAsyncClient:
    http_client = MockAsyncClient(
        get_responses=get_responses,
        post_responses=post_responses,
    )
    monkeypatch.setattr(
        eval_worker.httpx,
        "AsyncClient",
        MagicMock(return_value=http_client),
    )
    return http_client


@pytest.mark.asyncio
async def test_run_evaluate_loop_posts_required_summary_embedding(
    monkeypatch: pytest.MonkeyPatch, patched_runtime: dict[str, Any]
) -> None:
    http_client = _patch_http_client(
        monkeypatch,
        get_responses=_with_status(_response(200, json=_make_issue())),
        post_responses=[httpx.Response(status_code=200)],
    )

    await eval_worker.run_evaluate_loop(**DEFAULT_KWARGS)

    eval_worker.EmbeddingClient.assert_called_once_with(
        base_url="https://openrouter.ai/api/v1",
        model="openai/text-embedding-3-small",
        api_key="test-openrouter-key",
        ca_cert="",
    )
    patched_runtime["embed_client"].embed.assert_awaited_once_with(
        "Test issue. This is a test summary for the issue evaluation.",
        dimensions=1024,
    )
    posted = http_client.post.await_args.kwargs["json"]
    assert posted["summary_embedding"] == [0.1, 0.2, 0.3]
    assert posted["llm_backend"] == "local"


@pytest.mark.asyncio
async def test_run_evaluate_loop_uses_requested_concurrency(
    monkeypatch: pytest.MonkeyPatch, patched_runtime: dict[str, Any]
) -> None:
    http_client = _patch_http_client(
        monkeypatch,
        get_responses=_with_status(
            _response(200, json=_make_issue(external_id="100")),
            _response(200, json=_make_issue(issue_id=43, external_id="101")),
        ),
        post_responses=[
            httpx.Response(status_code=200),
            httpx.Response(status_code=200),
        ],
    )

    await eval_worker.run_evaluate_loop(
        **{**DEFAULT_KWARGS, "limit": 2, "concurrency": 2}
    )

    assert patched_runtime["evaluator"].evaluate.await_count == 2
    assert http_client.post.await_count == 2
