"""Tests for scripts.llm.eval_worker."""

from __future__ import annotations

import pathlib
from copy import deepcopy
from types import SimpleNamespace
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
        "impact": 40,
        "readiness": 50,
        "confidence": 80,
    },
    "suggested_action": "needs_triage",
    "suggested_action_reason": "Needs investigation by maintainer",
    "tokens_used": 500,
    "prompt_tokens": 300,
    "completion_tokens": 200,
    "cost_usd": 0.0042,
    "issue_data_hash": "unit_test_eval_hash",
    "related_work": [],
    "transcript": None,
}

DEFAULT_KWARGS = {
    "server": "http://localhost:8000",
    "token": "test-token",
    "model_summary": "summary-model",
    "model_scoring": "scoring-model",
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


@pytest.fixture
def base_runtime() -> SimpleNamespace:
    return SimpleNamespace(
        client=SimpleNamespace(retry_callback=None),
        evaluator=SimpleNamespace(
            evaluate=AsyncMock(
                return_value={
                    **SAMPLE_EVALUATE_RESULT,
                    "related_work": [],
                    "transcript": None,
                    "tool_context": SimpleNamespace(touched_paths=set()),
                }
            )
        ),
        embed_client=MagicMock(),
        http_client=MagicMock(),
        headers={"Authorization": "Bearer test-token"},
        params={},
        progress=MagicMock(update=MagicMock(), console=MagicMock(print=MagicMock())),
        overall_id=0,
        timing=MagicMock(add=MagicMock()),
        state=SimpleNamespace(
            release=AsyncMock(),
            complete=AsyncMock(return_value=1),
        ),
        poll_interval=1,
        issue_limit=1,
        model="scoring-model",
        llm_backend="local",
        mirror_dir=pathlib.Path("/mirrors"),
        allowed_projects={"snapcraft": "canonical/snapcraft"},
        eval_server_base_url="http://localhost:8000",
        single_issue=False,
    )


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
_RELATED_RESPONSE = httpx.Response(status_code=200, json={"results": []})

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
        get_responses=_with_status(
            _response(200, json=_make_issue()),
            _RELATED_RESPONSE,
        ),
        post_responses=[httpx.Response(status_code=200)],
    )

    await eval_worker.run_evaluate_loop(**DEFAULT_KWARGS)

    eval_worker.EmbeddingClient.assert_called_once_with(
        base_url="https://openrouter.ai/api/v1",
        model="openai/text-embedding-3-small",
        api_key="test-openrouter-key",
        ca_cert="",
    )
    patched_runtime["embed_client"].embed.assert_any_await(
        "Test issue. This is a test summary for the issue evaluation.",
        dimensions=1024,
    )
    patched_runtime["embed_client"].embed.assert_any_await(
        "Test issue\n\nTest body content here for the issue",
        dimensions=1024,
    )
    posted = http_client.post.await_args.kwargs["json"]
    assert posted["summary_embedding"] == [0.1, 0.2, 0.3]
    assert posted["search_embedding"] == [0.1, 0.2, 0.3]
    assert posted["llm_backend"] == "local"
    assert posted["cost_usd"] == SAMPLE_EVALUATE_RESULT["cost_usd"]


@pytest.mark.asyncio
async def test_llm_quota_error_pauses_instead_of_crash_looping(
    monkeypatch: pytest.MonkeyPatch, patched_runtime: dict[str, Any]
) -> None:
    """A quota error must back off (pause + sleep) rather than retry instantly.

    Regression test: previously ``LLMQuotaError`` fell through the generic
    ``except Exception`` in ``_evaluate_issue`` with no backoff at all, so
    the worker immediately reclaimed and re-failed the next issue in a tight
    loop until the quota reset on its own hours later.
    """
    http_client = _patch_http_client(
        monkeypatch,
        get_responses=_with_status(
            _response(200, json=_make_issue(external_id="100")),
            _RELATED_RESPONSE,
            _response(200, json=_make_issue(external_id="100")),
            _RELATED_RESPONSE,
        ),
        post_responses=[
            httpx.Response(status_code=200),
            httpx.Response(status_code=200),
        ],
    )
    patched_runtime["evaluator"].evaluate = AsyncMock(
        side_effect=[
            eval_worker.LLMQuotaError("OpenRouter daily quota exhausted."),
            SAMPLE_EVALUATE_RESULT,
        ]
    )

    await eval_worker.run_evaluate_loop(**DEFAULT_KWARGS)

    # The backoff sleep must have been invoked (not skipped) ...
    patched_runtime["sleep"].assert_awaited()
    # ... and the worker must have resumed and completed the retried issue
    # rather than getting stuck paused or crashing.
    assert patched_runtime["evaluator"].evaluate.await_count == 2
    # One POST reports the quota pause to the server, the other submits the
    # successful (retried) evaluation result.
    assert http_client.post.await_count == 2
    assert eval_worker.paused_state["paused"] is False
    # Backs off for a fixed 30 minutes, not "until tomorrow".
    assert eval_worker._QUOTA_BACKOFF_SECONDS == 30 * 60
    quota_pause_call = next(
        call
        for call in http_client.post.await_args_list
        if call.args[0] == "/api/eval/quota-pause"
    )
    assert quota_pause_call.kwargs["json"]["reason"] == "quota"


@pytest.mark.asyncio
async def test_run_evaluate_loop_uses_requested_concurrency(
    monkeypatch: pytest.MonkeyPatch, patched_runtime: dict[str, Any]
) -> None:
    http_client = _patch_http_client(
        monkeypatch,
        get_responses=_with_status(
            _response(200, json=_make_issue(external_id="100")),
            _RELATED_RESPONSE,
            _response(200, json=_make_issue(issue_id=43, external_id="101")),
            _RELATED_RESPONSE,
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


@pytest.mark.asyncio
async def test_run_evaluate_loop_posts_serialized_evidence_paths(
    monkeypatch: pytest.MonkeyPatch, patched_runtime: dict[str, Any]
) -> None:
    http_client = _patch_http_client(
        monkeypatch,
        get_responses=_with_status(
            _response(200, json=_make_issue()),
            _RELATED_RESPONSE,
        ),
        post_responses=[httpx.Response(status_code=200)],
    )
    patched_runtime["evaluator"].evaluate = AsyncMock(
        return_value={
            **SAMPLE_EVALUATE_RESULT,
            "tool_context": SimpleNamespace(
                touched_paths={
                    ("canonical/rockcraft", "README.md"),
                    ("canonical/rockcraft", "src/parts.py"),
                }
            ),
        }
    )

    await eval_worker.run_evaluate_loop(**DEFAULT_KWARGS)

    posted = http_client.post.await_args.kwargs["json"]
    assert posted["evidence_paths"] == [
        {"repo": "canonical/rockcraft", "path": "README.md"},
        {"repo": "canonical/rockcraft", "path": "src/parts.py"},
    ]


@pytest.mark.asyncio
async def test_evaluate_issue_passes_project_and_tool_ctx(
    monkeypatch: pytest.MonkeyPatch, base_runtime: SimpleNamespace
) -> None:
    monkeypatch.setattr(eval_worker, "_embed_summary", AsyncMock(return_value=[0.1]))
    monkeypatch.setattr(
        eval_worker, "_embed_search_text", AsyncMock(return_value=[0.2])
    )
    post_submission = AsyncMock(return_value=_response(200))
    monkeypatch.setattr(eval_worker, "_post_submission", post_submission)

    issue_data = _make_issue(
        project_name="snapcraft",
        repo_shas={"snapcraft": "a" * 40},
        issue_id=7,
    )
    await eval_worker._evaluate_issue(
        base_runtime, issue_data=issue_data, worker_name="worker-1"
    )

    call_kwargs = base_runtime.evaluator.evaluate.call_args.kwargs
    assert call_kwargs["project"] == "snapcraft"
    tool_ctx = call_kwargs["tool_ctx"]
    assert tool_ctx.mirror_dir == pathlib.Path("/mirrors")
    assert tool_ctx.allowed_projects == {"snapcraft": "canonical/snapcraft"}
    assert tool_ctx.pinned_shas == {"snapcraft": "a" * 40}
    assert tool_ctx.eval_server_base_url == "http://localhost:8000"
    assert tool_ctx.eval_api_token == "test-token"
    assert tool_ctx.issue_id == 7
    submission = post_submission.await_args.kwargs["submission"]
    assert submission["related_work"] == []
    assert submission["transcript"] is None


@pytest.mark.asyncio
async def test_evaluate_issue_discards_on_tool_failure(
    monkeypatch: pytest.MonkeyPatch, base_runtime: SimpleNamespace
) -> None:
    base_runtime.evaluator.evaluate = AsyncMock(
        side_effect=eval_worker.EvaluationDiscarded("tool failure")
    )
    post_submission = AsyncMock()
    monkeypatch.setattr(eval_worker, "_post_submission", post_submission)

    await eval_worker._evaluate_issue(
        base_runtime,
        issue_data=_make_issue(repo_shas={"snapcraft": "a" * 40}),
        worker_name="worker-1",
    )

    base_runtime.state.release.assert_awaited_once()
    post_submission.assert_not_called()


@pytest.mark.asyncio
async def test_worker_loop_skips_evaluate_when_preflight_blocks(
    monkeypatch: pytest.MonkeyPatch, base_runtime: SimpleNamespace
) -> None:
    base_runtime.state.reserve = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(
        eval_worker,
        "_fetch_next_issue",
        AsyncMock(return_value=_make_issue(repo_shas={"snapcraft": "b" * 40})),
    )
    monkeypatch.setattr(
        eval_worker, "_run_issue_preflight", AsyncMock(return_value=False)
    )
    evaluate_issue = AsyncMock()
    monkeypatch.setattr(eval_worker, "_evaluate_issue", evaluate_issue)

    await eval_worker._worker_loop(
        base_runtime, server_url="http://localhost:8000", worker_index=1
    )

    evaluate_issue.assert_not_called()
