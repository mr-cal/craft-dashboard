"""Characterization tests for current HTTP evaluation worker transitions."""

from __future__ import annotations

import asyncio
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
    "repo_shas": {},
}

SAMPLE_RESULT = {
    "summary": "This is a test summary for the issue evaluation.",
    "scores": {
        "impact": 40,
        "complexity": 30,
        "actionability": 70,
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
    "server": "http://localhost:8000/",
    "token": "test-token",
    "model_summary": "summary-model",
    "model_scoring": "scoring-model",
    "llm_backend": "local",
    "llm_url": "http://localhost:11434/v1",
    "llm_api_key": "",
    "ca_cert": "",
    "poll_interval": 1,
    "limit": 2,
    "project": "snapcraft",
    "open_only": True,
    "force": False,
    "incomplete": False,
    "stale_days": 0,
    "server_ca_cert": "",
    "verbose": False,
    "openrouter_api_key": "test-openrouter-key",
    "concurrency": 3,
}

_DUMMY_REQUEST = httpx.Request("POST", "http://localhost:8000/api/eval/result")


class MockAsyncClient:
    def __init__(self) -> None:
        self.get = AsyncMock(
            side_effect=[
                httpx.Response(200, json={"projects": {}}, request=_DUMMY_REQUEST),
                httpx.Response(
                    200,
                    json={"pending": 8, "total_open": 10, "total_evaluated": 2},
                    request=_DUMMY_REQUEST,
                ),
            ]
        )
        self.post = AsyncMock(return_value=httpx.Response(200, request=_DUMMY_REQUEST))

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb


def _issue(**updates: Any) -> dict[str, Any]:
    issue = deepcopy(SAMPLE_ISSUE)
    issue.update(updates)
    return issue


def _response(status_code: int, **kwargs: Any) -> httpx.Response:
    return httpx.Response(status_code, request=_DUMMY_REQUEST, **kwargs)


@pytest.fixture(autouse=True)
def _reset_worker_state() -> None:
    eval_worker.shutdown_state["requested"] = False
    eval_worker.paused_state["paused"] = False
    eval_worker._circuit_breaker_active = False
    eval_worker._quota_paused = False


@pytest.fixture
def runtime() -> SimpleNamespace:
    return SimpleNamespace(
        client=SimpleNamespace(retry_callback=None),
        evaluator=SimpleNamespace(
            evaluate=AsyncMock(
                return_value={
                    **SAMPLE_RESULT,
                    "tool_context": SimpleNamespace(touched_paths=set()),
                }
            )
        ),
        http_client=SimpleNamespace(
            get=AsyncMock(return_value=_response(200, json=_issue())),
            post=AsyncMock(return_value=_response(200)),
        ),
        headers={"Authorization": "Bearer test-token"},
        params={"project": "snapcraft"},
        progress=MagicMock(update=MagicMock(), console=MagicMock(print=MagicMock())),
        overall_id=0,
        timing=MagicMock(add=MagicMock()),
        state=SimpleNamespace(
            release=AsyncMock(),
            complete=AsyncMock(return_value=1),
            record_failure=AsyncMock(return_value=1),
            consecutive_failures=0,
            lock=asyncio.Lock(),
        ),
        poll_interval=7,
        issue_limit=2,
        model="scoring-model",
        llm_backend="local",
        mirror_dir=pathlib.Path("mirrors"),
        allowed_projects={"snapcraft": "canonical/snapcraft"},
        eval_server_base_url="http://localhost:8000",
        single_issue=False,
        slow_eval=False,
        min_delay=25.0,
        max_delay=55.0,
    )


async def test_fetch_next_issue_releases_reserved_slot_and_polls_on_no_content(
    monkeypatch: pytest.MonkeyPatch, runtime: SimpleNamespace
) -> None:
    runtime.http_client.get = AsyncMock(return_value=_response(204))
    sleep = AsyncMock()
    monkeypatch.setattr(eval_worker, "_sleep_until_next_poll", sleep)

    assert (
        await eval_worker._fetch_next_issue(runtime, server_url="http://server") is None
    )

    runtime.state.release.assert_awaited_once()
    sleep.assert_awaited_once_with(runtime.poll_interval)


async def test_fetch_next_issue_rate_limit_uses_six_times_poll_interval(
    monkeypatch: pytest.MonkeyPatch, runtime: SimpleNamespace
) -> None:
    runtime.http_client.get = AsyncMock(return_value=_response(429))
    sleep = AsyncMock()
    monkeypatch.setattr(eval_worker, "_sleep_until_next_poll", sleep)

    assert (
        await eval_worker._fetch_next_issue(runtime, server_url="http://server") is None
    )

    runtime.state.release.assert_awaited_once()
    sleep.assert_awaited_once_with(runtime.poll_interval * 6)


async def test_post_submission_retries_only_gateway_errors_with_exponential_sleeps(
    monkeypatch: pytest.MonkeyPatch, runtime: SimpleNamespace
) -> None:
    runtime.http_client.post = AsyncMock(
        side_effect=[_response(502), _response(504), _response(200)]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(eval_worker.asyncio, "sleep", sleep)

    response = await eval_worker._post_submission(
        runtime,
        issue_ref="snapcraft#100",
        submission={"issue_id": 42},
    )

    assert response is not None
    assert response.status_code == 200
    assert runtime.http_client.post.await_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [2, 4]


async def test_post_submission_retries_server_error_up_to_max_attempts(
    monkeypatch: pytest.MonkeyPatch, runtime: SimpleNamespace
) -> None:
    runtime.http_client.post = AsyncMock(return_value=_response(500))
    sleep = AsyncMock()
    monkeypatch.setattr(eval_worker.asyncio, "sleep", sleep)

    response = await eval_worker._post_submission(
        runtime,
        issue_ref="snapcraft#100",
        submission={"issue_id": 42},
    )

    assert response is not None
    assert response.status_code == 500
    assert runtime.http_client.post.await_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [2, 4]


async def test_post_submission_does_not_retry_client_error(
    monkeypatch: pytest.MonkeyPatch, runtime: SimpleNamespace
) -> None:
    runtime.http_client.post = AsyncMock(return_value=_response(400))
    sleep = AsyncMock()
    monkeypatch.setattr(eval_worker.asyncio, "sleep", sleep)

    response = await eval_worker._post_submission(
        runtime,
        issue_ref="snapcraft#100",
        submission={"issue_id": 42},
    )

    assert response is not None
    assert response.status_code == 400
    runtime.http_client.post.assert_awaited_once()
    sleep.assert_not_awaited()


async def test_evaluate_issue_failure_releases_claim_and_records_failure(
    monkeypatch: pytest.MonkeyPatch, runtime: SimpleNamespace
) -> None:
    runtime.evaluator.evaluate = AsyncMock(side_effect=RuntimeError("boom"))
    release_claim = AsyncMock()
    monkeypatch.setattr(eval_worker, "_release_claim", release_claim)

    result = await eval_worker._evaluate_issue(
        runtime, issue_data=_issue(), worker_name="worker-1"
    )

    assert result is False
    runtime.state.release.assert_awaited_once()
    runtime.state.record_failure.assert_awaited_once()
    release_claim.assert_awaited_once_with(
        runtime,
        issue_id=42,
        issue_ref="snapcraft#100",
        reason="evaluation_error",
    )


async def test_evaluate_issue_submit_network_error_releases_claim_and_not_complete(
    monkeypatch: pytest.MonkeyPatch, runtime: SimpleNamespace
) -> None:
    monkeypatch.setattr(eval_worker, "_post_submission", AsyncMock(return_value=None))
    release_claim = AsyncMock()
    monkeypatch.setattr(eval_worker, "_release_claim", release_claim)

    result = await eval_worker._evaluate_issue(
        runtime, issue_data=_issue(), worker_name="worker-1"
    )

    assert result is False
    runtime.state.release.assert_awaited_once()
    runtime.state.record_failure.assert_awaited_once()
    runtime.state.complete.assert_not_awaited()
    release_claim.assert_awaited_once_with(
        runtime,
        issue_id=42,
        issue_ref="snapcraft#100",
        reason="submit_network_error",
    )


async def test_evaluate_issue_conflict_releases_claim_but_counts_as_success(
    monkeypatch: pytest.MonkeyPatch, runtime: SimpleNamespace
) -> None:
    monkeypatch.setattr(
        eval_worker, "_post_submission", AsyncMock(return_value=_response(409))
    )
    release_claim = AsyncMock()
    monkeypatch.setattr(eval_worker, "_release_claim", release_claim)

    result = await eval_worker._evaluate_issue(
        runtime, issue_data=_issue(), worker_name="worker-1"
    )

    # NOTE: looks suspect -- the worker reports success to its loop even though
    # no evaluation was stored and the run-state completed count is not advanced.
    assert result is True
    runtime.state.release.assert_awaited_once()
    runtime.state.record_failure.assert_not_awaited()
    runtime.state.complete.assert_not_awaited()
    release_claim.assert_awaited_once_with(
        runtime,
        issue_id=42,
        issue_ref="snapcraft#100",
        reason="content_changed",
    )


async def test_evaluate_issue_success_completes_tokens_and_shutdowns_at_limit(
    monkeypatch: pytest.MonkeyPatch, runtime: SimpleNamespace
) -> None:
    runtime.issue_limit = 1
    monkeypatch.setattr(
        eval_worker, "_post_submission", AsyncMock(return_value=_response(200))
    )
    release_claim = AsyncMock()
    monkeypatch.setattr(eval_worker, "_release_claim", release_claim)

    result = await eval_worker._evaluate_issue(
        runtime, issue_data=_issue(), worker_name="worker-1"
    )

    assert result is True
    runtime.state.complete.assert_awaited_once_with(
        prompt_tokens=300,
        completion_tokens=200,
    )
    release_claim.assert_not_awaited()
    assert eval_worker.shutdown_state["requested"] is True


async def test_worker_loop_sleeps_failure_backoff_after_failed_evaluation(
    monkeypatch: pytest.MonkeyPatch, runtime: SimpleNamespace
) -> None:
    runtime.state.reserve = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(
        eval_worker, "_fetch_next_issue", AsyncMock(return_value=_issue())
    )
    monkeypatch.setattr(
        eval_worker, "_run_issue_preflight", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(eval_worker, "_evaluate_issue", AsyncMock(return_value=False))
    sleep = AsyncMock()
    monkeypatch.setattr(eval_worker, "_sleep_until_next_poll", sleep)

    await eval_worker._worker_loop(runtime, server_url="http://server", worker_index=1)

    sleep.assert_awaited_once_with(eval_worker._FAILURE_BACKOFF_SECONDS)


async def test_worker_loop_skips_failure_backoff_when_failure_requests_shutdown(
    monkeypatch: pytest.MonkeyPatch, runtime: SimpleNamespace
) -> None:
    runtime.state.reserve = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(
        eval_worker, "_fetch_next_issue", AsyncMock(return_value=_issue())
    )
    monkeypatch.setattr(
        eval_worker, "_run_issue_preflight", AsyncMock(return_value=True)
    )

    async def _fail_and_shutdown(*args: object, **kwargs: object) -> bool:
        del args, kwargs
        eval_worker.shutdown_state["requested"] = True
        return False

    monkeypatch.setattr(
        eval_worker, "_evaluate_issue", AsyncMock(side_effect=_fail_and_shutdown)
    )
    sleep = AsyncMock()
    monkeypatch.setattr(eval_worker, "_sleep_until_next_poll", sleep)

    await eval_worker._worker_loop(runtime, server_url="http://server", worker_index=1)

    sleep.assert_not_awaited()


async def test_run_state_reserve_enforces_limit_and_release_reopens_slot() -> None:
    state = eval_worker._RunState(limit=2)

    assert await state.reserve() is True
    assert await state.reserve() is True
    assert await state.reserve() is False
    await state.release()
    assert await state.reserve() is True


async def test_run_evaluate_loop_passes_filters_concurrency_and_issue_limit_to_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock(close=AsyncMock(), check_quota=AsyncMock())
    progress = MagicMock()
    progress.__enter__ = MagicMock(return_value=progress)
    progress.__exit__ = MagicMock(return_value=False)
    progress.add_task = MagicMock(return_value=0)
    progress.console = MagicMock()
    http_client = MockAsyncClient()
    seen_runtimes = []

    async def _record_runtime(
        runtime: object, *, server_url: str, worker_index: int
    ) -> None:
        del server_url, worker_index
        seen_runtimes.append(runtime)

    monkeypatch.setattr(eval_worker.signal, "signal", MagicMock())
    monkeypatch.setattr(eval_worker, "_setup_logging", MagicMock(return_value=None))
    monkeypatch.setattr(eval_worker, "_make_progress", MagicMock(return_value=progress))
    monkeypatch.setattr(eval_worker, "_start_keyboard_monitor", MagicMock())
    monkeypatch.setattr(
        eval_worker, "create_llm_client_for_backend", MagicMock(return_value=client)
    )
    monkeypatch.setattr(
        eval_worker, "IssueEvaluator", MagicMock(return_value=MagicMock())
    )
    monkeypatch.setattr(
        eval_worker,
        "load_config",
        MagicMock(return_value=SimpleNamespace(craft_projects={})),
    )
    monkeypatch.setattr(
        eval_worker,
        "resolve_allowed_projects",
        MagicMock(return_value={"snapcraft": "canonical/snapcraft"}),
    )
    monkeypatch.setattr(
        eval_worker.httpx, "AsyncClient", MagicMock(return_value=http_client)
    )
    monkeypatch.setattr(
        eval_worker, "_worker_loop", AsyncMock(side_effect=_record_runtime)
    )

    await eval_worker.run_evaluate_loop(
        **{**DEFAULT_KWARGS, "issue": "123", "limit": 99}
    )

    assert len(seen_runtimes) == 3
    runtime = seen_runtimes[0]
    assert runtime.params == {
        "project": "snapcraft",
        "open_only": True,
        "force": False,
        "incomplete": False,
        "stale_days": 0,
        "external_id": "123",
    }
    assert runtime.issue_limit == 1
    # NOTE: looks suspect -- targeted issue runs force limit=1 but do not pass
    # single_issue=True into _Runtime, despite _Runtime documenting that mode.
    assert runtime.single_issue is False
    assert progress.add_task.call_args.kwargs["total"] == 1
    client.close.assert_awaited_once()
