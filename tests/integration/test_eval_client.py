"""Integration tests for the eval client (scripts/eval_client.py).

Tests the full client loop: fetch issue from API, evaluate with LLM, submit
result back. Uses real-world issue data structures from the craft-dashboard
eval API and mocks only the HTTP boundary and LLM layer.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from craft_dashboard.llm.embeddings import EmbeddingClient
from craft_dashboard.llm.evaluator import _compute_content_hash
from scripts import eval_client

# ---------------------------------------------------------------------------
# Real-world issue data captured from the craft-dashboard API
# ---------------------------------------------------------------------------

REAL_ISSUE_SNAPCRAFT = {
    "issue_id": 1451,
    "project_name": "snapcraft",
    "external_id": "2695",
    "title": "Build fails on arm64 after kernel update",
    "state": "open",
    "issue_type": "issue",
    "body": (
        "After updating to the latest kernel on Ubuntu 24.04, snapcraft "
        "builds fail with 'module not found' errors. This affects all "
        "projects using the latest build plugins. "
        "\n\n## Steps to reproduce\n"
        "1. Update kernel to 6.8.0-117-generic\n"
        "2. Run `snapcraft build`\n"
        "3. Observe module import failures\n\n"
        "## Expected behavior\nBuilds should complete successfully.\n\n"
        "## Actual behavior\nBuild fails with import errors."
    ),
    "comments": [
        {
            "author": "bob-remote",
            "body": "I can reproduce this on my end too.",
            "created_at": "2026-05-10T14:30:00+00:00",
            "updated_at": "2026-05-10T14:30:00+00:00",
        },
        {
            "author": "alice-canonical",
            "body": "This looks related to snapd changes. Investigating.",
            "created_at": "2026-05-12T09:15:00+00:00",
            "updated_at": "2026-05-12T09:15:00+00:00",
        },
    ],
    "labels": ["bug", "arm64", "regression"],
    "author": "bob-remote",
    "author_association": "CONTRIBUTOR",
    "created_at": "2026-04-01T10:00:00+00:00",
    "updated_at": "2026-05-12T09:15:00+00:00",
    "current_hash": "219a6a5fdaa8390c",
    "maintainers": ["alice-canonical", "bob-canonical"],
}

DEFAULT_KWARGS = {
    "server": "http://localhost:8000",
    "token": "test-token",
    "summary_model": "test-model",
    "evaluation_model": "test-model",
    "embed_model": "",
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

REAL_ISSUE_LANDSCAPE = {
    "issue_id": 1452,
    "project_name": "landscape",
    "external_id": "2694",
    "title": "Dashboard shows incorrect CPU usage",
    "state": "open",
    "issue_type": "issue",
    "body": (
        "The system dashboard in Landscape shows CPU usage at 100% "
        "continuously, but top/htop show normal values around 15%. "
        "This started after upgrading to version 24.04."
    ),
    "comments": [],
    "labels": ["bug", "ui"],
    "author": "viewer-user",
    "author_association": "CONTRIBUTOR",
    "created_at": "2026-03-15T08:00:00+00:00",
    "updated_at": "2026-03-15T08:00:00+00:00",
    "current_hash": "0e0a2d893600992e",
    "maintainers": ["alice-canonical"],
}

REAL_ISSUE_CHARM = {
    "issue_id": 1453,
    "project_name": "charmhub",
    "external_id": "2693",
    "title": "Support request: How to deploy multi-charm stack",
    "state": "open",
    "issue_type": "issue",
    "body": (
        "I am trying to deploy a stack of three charms together. "
        "The documentation mentions `charm-tools` but I cannot find "
        "clear instructions. Can someone point me to the right docs?"
    ),
    "comments": [
        {
            "author": "new-contributor",
            "body": "Same question here.",
            "created_at": "2026-05-20T16:00:00+00:00",
            "updated_at": "2026-05-20T16:00:00+00:00",
        },
    ],
    "labels": ["question", "documentation"],
    "author": "new-contributor",
    "author_association": "CONTRIBUTOR",
    "created_at": "2026-05-20T15:00:00+00:00",
    "updated_at": "2026-05-20T16:00:00+00:00",
    "current_hash": "25de68e5fc58cfa0",
    "maintainers": ["alice-canonical", "bob-canonical"],
}

REAL_ISSUE_PR = {
    "issue_id": 1454,
    "project_name": "snapcraft",
    "external_id": "2692",
    "title": "feat: Add support for newer kernel modules",
    "state": "open",
    "issue_type": "pull_request",
    "body": (
        "This PR adds support for kernel modules loaded after 6.8.0. "
        "Changes include updating module detection logic and adding "
        "new test cases.\n\n## Changes\n- Update module detection\n"
        "- Add integration tests\n- Bump version"
    ),
    "comments": [
        {
            "author": "alice-canonical",
            "body": "LGTM, just one nit about the test naming.",
            "created_at": "2026-06-01T12:00:00+00:00",
            "updated_at": "2026-06-01T12:00:00+00:00",
        },
    ],
    "labels": ["enhancement", "ready-for-review"],
    "author": "dev-contrib",
    "author_association": "CONTRIBUTOR",
    "created_at": "2026-05-28T10:00:00+00:00",
    "updated_at": "2026-06-01T12:00:00+00:00",
    "current_hash": "a127efca396f446d",
    "maintainers": ["alice-canonical"],
}

SAMPLE_SUMMARIZE_RESULT = (
    "This is a detailed summary of the issue and its context.",
    300,
    200,
    100,
)

SAMPLE_SCORE_RESULT = (
    {
        "scores": {
            "staleness": 45,
            "complexity": 30,
            "support_request": 80,
            "confidence": 65,
        },
        "suggested_action": "needs_triage",
        "suggested_action_reason": "Issue lacks maintainer labels and has no "
        "prior assessment from the core team.",
    },
    200,
    100,
    100,
)

# Keep for backward-compat with hash/field assertion helpers below
SAMPLE_EVAL_RESULT: dict[str, Any] = {
    "summary": SAMPLE_SUMMARIZE_RESULT[0],
    "scores": SAMPLE_SCORE_RESULT[0]["scores"],
    "suggested_action": "needs_triage",
    "suggested_action_reason": "Issue lacks maintainer labels and has no "
    "prior assessment from the core team.",
    "tokens_used": 500,
    "prompt_tokens": 300,
    "completion_tokens": 200,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_issue(**overrides: Any) -> dict[str, Any]:
    """Return a copy of the snapcraft issue with optional overrides."""
    result = deepcopy(REAL_ISSUE_SNAPCRAFT)
    result.update(overrides)
    return result


@pytest.fixture(autouse=True)
def _reset_shutdown_state() -> None:
    eval_client.shutdown_state["requested"] = False
    eval_client.paused_state["paused"] = False


_STATUS_RESPONSE = httpx.Response(
    status_code=200,
    json={
        "pending": 1179,
        "locked": 0,
        "evaluated_today": 362,
        "total_evaluated": 1955,
        "total_open": 3134,
    },
)

_DUMMY_REQUEST = httpx.Request("GET", "http://localhost:8000/api/eval/next")


def _response(status_code: int, **kwargs: Any) -> httpx.Response:
    """Create an httpx.Response with a request attached (needed for response.url)."""
    r = httpx.Response(status_code=status_code, **kwargs)
    r.request = _DUMMY_REQUEST
    return r


def _with_status(*responses: httpx.Response) -> list[httpx.Response]:
    """Prepend a status response to a list of API responses."""
    return [_STATUS_RESPONSE, *responses]


@pytest.fixture
def patched_runtime(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch all external dependencies of run_eval_loop.

    Leaves the client's HTTP interaction loop intact (via MockAsyncClient),
    but replaces the LLM client, the evaluator, progress rendering, timing,
    sleep, and signal handling.
    """
    llm_client = MagicMock()
    llm_client.close = AsyncMock()

    evaluator = MagicMock()
    evaluator.summarize = AsyncMock(return_value=SAMPLE_SUMMARIZE_RESULT)
    evaluator.score = AsyncMock(return_value=SAMPLE_SCORE_RESULT)

    sleep_mock = AsyncMock()

    mock_progress = MagicMock()
    mock_progress.__enter__ = MagicMock(return_value=mock_progress)
    mock_progress.__exit__ = MagicMock(return_value=False)
    mock_progress.add_task = MagicMock(return_value=0)
    mock_progress.update = MagicMock()

    # Capture console.print calls (used for per-issue completion lines)
    console_prints: list[str] = []
    mock_console = MagicMock()
    mock_console.print = lambda *args, **kwargs: console_prints.append(str(args[0]))
    mock_progress.console = mock_console

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
    monkeypatch.setattr(eval_client, "_setup_logging", MagicMock())

    return {
        "llm_client": llm_client,
        "evaluator": evaluator,
        "sleep": sleep_mock,
        "progress": mock_progress,
        "timing": mock_timing,
        "console_prints": console_prints,
    }


def _patch_http(
    monkeypatch: pytest.MonkeyPatch,
    *,
    get_responses: list[httpx.Response],
    post_responses: list[httpx.Response] | None = None,
) -> MagicMock:
    """Patch eval_client.httpx.AsyncClient and return a mock with get/post."""
    client_mock = MagicMock()
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=False)
    client_mock.get = AsyncMock(side_effect=get_responses)
    client_mock.post = AsyncMock(side_effect=post_responses or [])
    monkeypatch.setattr(
        eval_client.httpx, "AsyncClient", MagicMock(return_value=client_mock)
    )
    return client_mock


# ---------------------------------------------------------------------------
# Happy-path integration tests with real-world data
# ---------------------------------------------------------------------------


class TestEvalClientHappyPath:
    """Test the full client loop using realistic issue data."""

    @pytest.mark.asyncio
    async def test_evaluates_single_real_issue(
        self, monkeypatch, patched_runtime
    ) -> None:
        """A single real-world issue is fetched, evaluated, and submitted."""
        client_mock = _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=REAL_ISSUE_SNAPCRAFT)
            ),
            post_responses=[httpx.Response(status_code=200)],
        )

        await eval_client.run_eval_loop(**DEFAULT_KWARGS)

        # Evaluator was called with data derived from the real issue
        call_kwargs = patched_runtime["evaluator"].summarize.call_args.kwargs
        assert call_kwargs["title"] == "Build fails on arm64 after kernel update"
        assert call_kwargs["issue_type"] == "issue"
        assert call_kwargs["state"] == "open"
        assert call_kwargs["labels"] == ["bug", "arm64", "regression"]
        assert call_kwargs["author"] == "bob-remote"
        assert call_kwargs["is_maintainer"] is False
        assert call_kwargs["comment_count"] == 2
        assert len(call_kwargs["comments"]) == 2

        # Submission payload matches the evaluator result
        submit_json = client_mock.post.call_args.kwargs["json"]
        assert submit_json["issue_id"] == 1451
        assert submit_json["suggested_action"] == "needs_triage"
        assert submit_json["model_used"] == "test-model"
        assert submit_json["llm_backend"] == "local"

    @pytest.mark.asyncio
    async def test_processes_multiple_real_issues_in_sequence(
        self, monkeypatch, patched_runtime
    ) -> None:
        """Multiple issues from different projects are evaluated sequentially."""
        issues = [REAL_ISSUE_SNAPCRAFT, REAL_ISSUE_LANDSCAPE, REAL_ISSUE_CHARM]
        score_results = [
            (SAMPLE_SCORE_RESULT[0], 200, 100, 100),
            (
                {**SAMPLE_SCORE_RESULT[0], "suggested_action": "keep_open"},
                200,
                100,
                100,
            ),
            (
                {**SAMPLE_SCORE_RESULT[0], "suggested_action": "close_stale"},
                200,
                100,
                100,
            ),
        ]
        patched_runtime["evaluator"].score.side_effect = score_results

        get_responses = _with_status(
            httpx.Response(status_code=200, json=issues[0]),
            httpx.Response(status_code=200, json=issues[1]),
            httpx.Response(status_code=200, json=issues[2]),
            httpx.Response(status_code=204),
        )
        post_responses = [httpx.Response(status_code=200)] * 3

        _patch_http(
            monkeypatch, get_responses=get_responses, post_responses=post_responses
        )

        await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 3})

        assert patched_runtime["evaluator"].summarize.call_count == 3
        assert (
            patched_runtime["evaluator"].summarize.call_args_list[0].kwargs["title"]
            == issues[0]["title"]
        )
        assert (
            patched_runtime["evaluator"].summarize.call_args_list[1].kwargs["title"]
            == issues[1]["title"]
        )
        assert (
            patched_runtime["evaluator"].summarize.call_args_list[2].kwargs["title"]
            == issues[2]["title"]
        )

        # Verify each project name was extracted correctly
        calls = patched_runtime["evaluator"].summarize.call_args_list
        assert calls[0].kwargs["author"] == "bob-remote"
        assert calls[1].kwargs["author"] == "viewer-user"
        assert calls[2].kwargs["author"] == "new-contributor"

    @pytest.mark.asyncio
    async def test_evaluates_pr_with_real_data(
        self, monkeypatch, patched_runtime
    ) -> None:
        """A pull request issue is evaluated with correct issue_type."""
        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=REAL_ISSUE_PR)
            ),
            post_responses=[httpx.Response(status_code=200)],
        )

        await eval_client.run_eval_loop(**DEFAULT_KWARGS)

        call_kwargs = patched_runtime["evaluator"].summarize.call_args.kwargs
        assert call_kwargs["issue_type"] == "pull_request"
        assert call_kwargs["labels"] == ["enhancement", "ready-for-review"]

    @pytest.mark.asyncio
    async def test_maintainer_author_is_maintainer_flag(
        self, monkeypatch, patched_runtime
    ) -> None:
        """Author who is a maintainer gets is_maintainer=True."""
        maintainer_issue = _make_issue(
            issue_id=9999,
            external_id="9999",
            author="alice-canonical",
            author_association="MAINTAINER",
            maintainers=["alice-canonical"],
        )
        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=maintainer_issue)
            ),
            post_responses=[httpx.Response(status_code=200)],
        )

        await eval_client.run_eval_loop(**DEFAULT_KWARGS)

        call_kwargs = patched_runtime["evaluator"].summarize.call_args.kwargs
        assert call_kwargs["is_maintainer"] is True
        assert call_kwargs["author"] == "alice-canonical"

    @pytest.mark.asyncio
    async def test_evaluator_exception_is_logged_and_continues(
        self, monkeypatch, patched_runtime, caplog
    ) -> None:
        """If the evaluator raises, the error is logged and the next issue is fetched."""
        patched_runtime["evaluator"].summarize.side_effect = [
            Exception("LLM timeout"),
            SAMPLE_SUMMARIZE_RESULT,
        ]
        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=REAL_ISSUE_SNAPCRAFT),
                httpx.Response(status_code=200, json=REAL_ISSUE_LANDSCAPE),
                httpx.Response(status_code=204),
            ),
            post_responses=[httpx.Response(status_code=200)],
        )

        with caplog.at_level(logging.ERROR):
            await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 1})

        assert "LLM timeout" in caplog.text
        # Summarize was called twice (error + success), but only one was submitted
        assert patched_runtime["evaluator"].summarize.call_count == 2
        assert (
            patched_runtime["evaluator"].summarize.call_args_list[0].kwargs["title"]
            == REAL_ISSUE_SNAPCRAFT["title"]
        )


# ---------------------------------------------------------------------------
# Error-path integration tests
# ---------------------------------------------------------------------------


class TestEvalClientErrorPaths:
    """Test error handling in the client loop with realistic scenarios."""

    @pytest.mark.asyncio
    async def test_server_returns_401_unauthorized(
        self, monkeypatch, patched_runtime
    ) -> None:
        """401 from the API is logged and the loop sleeps."""

        async def request_shutdown(seconds: int) -> None:
            eval_client.shutdown_state["requested"] = True

        patched_runtime["sleep"].side_effect = request_shutdown
        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                _response(401, json={"detail": "Not authenticated"}),
            ),
        )

        with patch.object(eval_client, "logger") as mock_logger:
            await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 0})

        mock_logger.error.assert_called_once()
        assert "401" in str(mock_logger.error.call_args)

    @pytest.mark.asyncio
    async def test_submit_422_validation_error_is_logged(
        self, monkeypatch, patched_runtime, caplog
    ) -> None:
        """422 from the API (e.g. missing confidence) is logged as an error."""

        async def request_shutdown(seconds: int) -> None:
            eval_client.shutdown_state["requested"] = True

        patched_runtime["sleep"].side_effect = request_shutdown
        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=REAL_ISSUE_SNAPCRAFT),
                httpx.Response(status_code=204),
            ),
            post_responses=[
                _response(
                    422, json={"detail": "scores missing required keys: confidence"}
                )
            ],
        )

        with caplog.at_level(logging.ERROR):
            await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 0})

        assert "submit failed 422" in caplog.text
        assert "confidence" in caplog.text

    @pytest.mark.asyncio
    async def test_submit_409_conflict_is_skipped(
        self, monkeypatch, patched_runtime, caplog
    ) -> None:
        """409 (content changed) is logged as a warning and the loop continues."""

        async def request_shutdown(seconds: int) -> None:
            eval_client.shutdown_state["requested"] = True

        patched_runtime["sleep"].side_effect = request_shutdown
        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=REAL_ISSUE_SNAPCRAFT),
                httpx.Response(status_code=204),
            ),
            post_responses=[
                _response(
                    409,
                    json={
                        "detail": "Content hash mismatch; issue content has changed."
                    },
                )
            ],
        )

        with caplog.at_level(logging.WARNING):
            await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 0})

        assert "content changed during evaluation" in caplog.text

    @pytest.mark.asyncio
    async def test_server_500_triggers_retry(
        self, monkeypatch, patched_runtime
    ) -> None:
        """A 500 on the next endpoint causes a retry, not a crash."""

        async def request_shutdown(seconds: int) -> None:
            eval_client.shutdown_state["requested"] = True

        patched_runtime["sleep"].side_effect = request_shutdown
        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                _response(500, text="Internal Server Error"),
            ),
            post_responses=[],
        )

        await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 0})

        # 500 causes a sleep before retrying
        patched_runtime["sleep"].assert_awaited_once()
        # Evaluator was NOT called (the retry never happened before shutdown)
        patched_runtime["evaluator"].summarize.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_json_from_server_is_handled(
        self, monkeypatch, patched_runtime
    ) -> None:
        """Non-JSON response body is logged as an error and the loop continues."""

        async def request_shutdown(seconds: int) -> None:
            eval_client.shutdown_state["requested"] = True

        patched_runtime["sleep"].side_effect = request_shutdown
        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                _response(200, text="not valid json at all"),
            ),
        )

        with patch.object(eval_client, "logger") as mock_logger:
            await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 0})

        mock_logger.error.assert_called_once()
        assert "invalid JSON" in str(mock_logger.error.call_args)

    @pytest.mark.asyncio
    async def test_connect_error_triggers_sleep(
        self, monkeypatch, patched_runtime
    ) -> None:
        """Connection errors cause the loop to sleep and retry."""

        async def request_shutdown(seconds: int) -> None:
            eval_client.shutdown_state["requested"] = True

        patched_runtime["sleep"].side_effect = request_shutdown
        client_mock = _patch_http(
            monkeypatch,
            get_responses=[],
        )
        client_mock.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        with patch.object(eval_client, "logger") as mock_logger:
            await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 0})

        assert "Cannot connect" in str(mock_logger.error.call_args)
        patched_runtime["sleep"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_hash_mismatch_is_logged_as_warning(
        self, monkeypatch, patched_runtime, caplog
    ) -> None:
        """When the server hash differs from the local hash, a warning is logged."""

        async def request_shutdown(seconds: int) -> None:
            eval_client.shutdown_state["requested"] = True

        patched_runtime["sleep"].side_effect = request_shutdown
        mismatched_issue = deepcopy(REAL_ISSUE_SNAPCRAFT)
        mismatched_issue["current_hash"] = "totally_different_hash"

        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=mismatched_issue),
                httpx.Response(status_code=204),
            ),
            post_responses=[httpx.Response(status_code=200)],
        )

        with caplog.at_level(logging.WARNING):
            await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 0})

        assert "hash mismatch" in caplog.text

    @pytest.mark.asyncio
    async def test_server_error_on_submit_does_not_count_as_evaluated(
        self, monkeypatch, patched_runtime, caplog
    ) -> None:
        """A failed submission does not increment the evaluated counter."""

        async def request_shutdown(seconds: int) -> None:
            eval_client.shutdown_state["requested"] = True

        patched_runtime["sleep"].side_effect = request_shutdown
        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=REAL_ISSUE_SNAPCRAFT),
                httpx.Response(status_code=204),
            ),
            post_responses=[_response(500, text="server error")],
        )

        with caplog.at_level(logging.ERROR):
            await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 0})

        assert "submit failed 500" in caplog.text
        # No completion line is printed on failed submission
        assert patched_runtime["console_prints"] == []


# ---------------------------------------------------------------------------
# Flow-control integration tests
# ---------------------------------------------------------------------------


class TestEvalClientFlowControl:
    """Test flow control: limits, no-work, shutdown, pause."""

    @pytest.mark.asyncio
    async def test_limit_zero_runs_until_no_more_work(
        self, monkeypatch, patched_runtime
    ) -> None:
        """limit=0 runs until the server returns 204 (no more work)."""

        async def request_shutdown(seconds: int) -> None:
            eval_client.shutdown_state["requested"] = True

        patched_runtime["sleep"].side_effect = request_shutdown
        # 1 status + 2 issue gets + 1 final 204 + 1 more 204 (after sleep+continue)
        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=REAL_ISSUE_SNAPCRAFT),
                httpx.Response(status_code=200, json=REAL_ISSUE_LANDSCAPE),
                httpx.Response(status_code=204),
                httpx.Response(status_code=204),
            ),
            post_responses=[
                httpx.Response(status_code=200),
                httpx.Response(status_code=200),
            ],
        )

        await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 0})

        assert patched_runtime["evaluator"].summarize.call_count == 2

    @pytest.mark.asyncio
    async def test_limit_two_stops_after_two(
        self, monkeypatch, patched_runtime, caplog
    ) -> None:
        """limit=N stops after evaluating N issues."""
        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=REAL_ISSUE_SNAPCRAFT),
                httpx.Response(status_code=200, json=REAL_ISSUE_LANDSCAPE),
                httpx.Response(status_code=200, json=REAL_ISSUE_CHARM),
            ),
            post_responses=[
                httpx.Response(status_code=200),
                httpx.Response(status_code=200),
            ],
        )

        with caplog.at_level(logging.INFO):
            await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 2})

        assert "Done: evaluated 2 issues" in caplog.text
        assert patched_runtime["evaluator"].summarize.call_count == 2
        # The third issue was never evaluated
        assert "charmhub" not in caplog.text

    @pytest.mark.asyncio
    async def test_no_work_sleeps_and_retries(
        self, monkeypatch, patched_runtime
    ) -> None:
        """When the server returns 204, the client sleeps before re-polling."""

        async def request_shutdown(seconds: int) -> None:
            eval_client.shutdown_state["requested"] = True

        patched_runtime["sleep"].side_effect = request_shutdown

        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=204),
            ),
        )

        await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 0})

        patched_runtime["sleep"].assert_awaited_once()
        patched_runtime["evaluator"].summarize.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_token_counts_accumulate_across_issues(
        self, monkeypatch, patched_runtime, caplog
    ) -> None:
        """Token counts are accumulated and logged at the end of the run."""
        summary_result_1 = (SAMPLE_SUMMARIZE_RESULT[0], 300, 200, 100)
        score_result_1 = (SAMPLE_SCORE_RESULT[0], 200, 100, 100)
        summary_result_2 = (SAMPLE_SUMMARIZE_RESULT[0], 400, 250, 150)
        score_result_2 = (SAMPLE_SCORE_RESULT[0], 300, 150, 150)
        patched_runtime["evaluator"].summarize.side_effect = [
            summary_result_1,
            summary_result_2,
        ]
        patched_runtime["evaluator"].score.side_effect = [
            score_result_1,
            score_result_2,
        ]

        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=REAL_ISSUE_SNAPCRAFT),
                httpx.Response(status_code=200, json=REAL_ISSUE_LANDSCAPE),
                httpx.Response(status_code=204),
            ),
            post_responses=[
                httpx.Response(status_code=200),
                httpx.Response(status_code=200),
            ],
        )

        with caplog.at_level(logging.INFO):
            await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 2})

        assert (
            "Run total: 2 issues, 700 in / 500 out tokens (1200 total)" in caplog.text
        )


# ---------------------------------------------------------------------------
# Submission payload integration tests
# ---------------------------------------------------------------------------


class TestEvalClientSubmissionPayload:
    """Test that submission payloads are correctly constructed from real issue data."""

    @pytest.mark.asyncio
    async def test_submission_includes_all_real_issue_fields(
        self, monkeypatch, patched_runtime
    ) -> None:
        """The submission payload carries the correct issue_id, hash, and metadata."""
        client_mock = _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=REAL_ISSUE_SNAPCRAFT)
            ),
            post_responses=[httpx.Response(status_code=200)],
        )

        await eval_client.run_eval_loop(**DEFAULT_KWARGS)

        submit_json = client_mock.post.call_args.kwargs["json"]

        # Core identifiers
        assert submit_json["issue_id"] == 1451
        assert submit_json["model_used"] == "test-model"
        assert submit_json["llm_backend"] == "local"
        assert submit_json["summary_embedding"] is None

        # Content hash is computed locally from issue data
        expected_hash = _compute_content_hash(
            REAL_ISSUE_SNAPCRAFT["title"],
            REAL_ISSUE_SNAPCRAFT.get("body"),
            REAL_ISSUE_SNAPCRAFT["state"].lower(),
            REAL_ISSUE_SNAPCRAFT.get("labels", []),
            REAL_ISSUE_SNAPCRAFT.get("comments"),
        )
        assert submit_json["content_hash"] == expected_hash

        # Evaluator result fields
        assert submit_json["summary"] == SAMPLE_EVAL_RESULT["summary"]
        assert submit_json["scores"] == SAMPLE_EVAL_RESULT["scores"]
        assert submit_json["suggested_action"] == SAMPLE_EVAL_RESULT["suggested_action"]
        assert (
            submit_json["suggested_action_reason"]
            == SAMPLE_EVAL_RESULT["suggested_action_reason"]
        )

        # Token fields: combined from summarize + score
        # summarize: tokens=300, prompt=200, completion=100
        # score:     tokens=200, prompt=100, completion=100
        assert submit_json["tokens_used"] == 500
        assert submit_json["prompt_tokens"] == 300
        assert submit_json["completion_tokens"] == 200

    @pytest.mark.asyncio
    async def test_submission_uses_locally_computed_hash_not_server_hash(
        self, monkeypatch, patched_runtime
    ) -> None:
        """The content_hash is computed locally from issue data, not from the server's current_hash."""
        client_mock = _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=REAL_ISSUE_SNAPCRAFT)
            ),
            post_responses=[httpx.Response(status_code=200)],
        )

        await eval_client.run_eval_loop(**DEFAULT_KWARGS)

        submit_json = client_mock.post.call_args.kwargs["json"]
        expected_hash = _compute_content_hash(
            REAL_ISSUE_SNAPCRAFT["title"],
            REAL_ISSUE_SNAPCRAFT.get("body"),
            REAL_ISSUE_SNAPCRAFT["state"].lower(),
            REAL_ISSUE_SNAPCRAFT.get("labels", []),
            REAL_ISSUE_SNAPCRAFT.get("comments"),
        )
        assert submit_json["content_hash"] == expected_hash
        # The server's current_hash is not reused verbatim
        assert submit_json["content_hash"] != REAL_ISSUE_SNAPCRAFT["current_hash"]

    @pytest.mark.asyncio
    async def test_submission_includes_embedding_when_embed_model_set(
        self, monkeypatch, patched_runtime
    ) -> None:
        """When embed_model is set, summary_embedding is computed and submitted."""
        fake_embedding = [0.42] * 1024

        client_mock = _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=REAL_ISSUE_SNAPCRAFT)
            ),
            post_responses=[httpx.Response(status_code=200)],
        )

        with patch.object(
            EmbeddingClient, "embed", AsyncMock(return_value=fake_embedding)
        ):
            await eval_client.run_eval_loop(
                **{**DEFAULT_KWARGS, "embed_model": "nomic-embed-text"}
            )

        submit_json = client_mock.post.call_args.kwargs["json"]
        assert submit_json["summary_embedding"] == fake_embedding

    @pytest.mark.asyncio
    async def test_submission_has_no_embedding_when_embed_model_empty(
        self, monkeypatch, patched_runtime
    ) -> None:
        """When embed_model is empty, summary_embedding is None."""
        client_mock = _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=REAL_ISSUE_SNAPCRAFT)
            ),
            post_responses=[httpx.Response(status_code=200)],
        )

        await eval_client.run_eval_loop(**DEFAULT_KWARGS)  # embed_model=""

        submit_json = client_mock.post.call_args.kwargs["json"]
        assert submit_json["summary_embedding"] is None


class TestEvalClientEdgeCases:
    """Edge cases using realistic data shapes from the API."""

    @pytest.mark.asyncio
    async def test_issue_with_no_comments(self, monkeypatch, patched_runtime) -> None:
        """Issues with empty comment lists are handled correctly."""
        empty_comments_issue = deepcopy(REAL_ISSUE_LANDSCAPE)
        empty_comments_issue["comments"] = []

        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=empty_comments_issue)
            ),
            post_responses=[httpx.Response(status_code=200)],
        )

        await eval_client.run_eval_loop(**DEFAULT_KWARGS)

        call_kwargs = patched_runtime["evaluator"].summarize.call_args.kwargs
        assert call_kwargs["comment_count"] == 0
        assert call_kwargs["comments"] == []

    @pytest.mark.asyncio
    async def test_issue_with_many_comments(self, monkeypatch, patched_runtime) -> None:
        """Issues with many comments pass all of them to the evaluator."""
        many_comments = deepcopy(REAL_ISSUE_SNAPCRAFT)
        many_comments["comments"] = [
            {
                "author": f"user-{i}",
                "body": f"Comment number {i} in the thread discussing this issue.",
                "created_at": f"2026-05-{10 + i:02d}T10:00:00+00:00",
                "updated_at": f"2026-05-{10 + i:02d}T10:00:00+00:00",
            }
            for i in range(15)
        ]
        many_comments["current_hash"] = "many_comments_hash"

        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=many_comments)
            ),
            post_responses=[httpx.Response(status_code=200)],
        )

        await eval_client.run_eval_loop(**DEFAULT_KWARGS)

        call_kwargs = patched_runtime["evaluator"].summarize.call_args.kwargs
        assert call_kwargs["comment_count"] == 15
        assert len(call_kwargs["comments"]) == 15

    @pytest.mark.asyncio
    async def test_issue_with_no_labels(self, monkeypatch, patched_runtime) -> None:
        """Issues with no labels get an empty list passed to the evaluator."""
        no_labels_issue = deepcopy(REAL_ISSUE_SNAPCRAFT)
        no_labels_issue["labels"] = []
        no_labels_issue["current_hash"] = "no_labels_hash"

        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=no_labels_issue)
            ),
            post_responses=[httpx.Response(status_code=200)],
        )

        await eval_client.run_eval_loop(**DEFAULT_KWARGS)

        call_kwargs = patched_runtime["evaluator"].summarize.call_args.kwargs
        assert call_kwargs["labels"] == []

    @pytest.mark.asyncio
    async def test_issue_with_no_body(self, monkeypatch, patched_runtime) -> None:
        """Issues with None body are handled without crashing."""
        no_body_issue = deepcopy(REAL_ISSUE_SNAPCRAFT)
        no_body_issue["body"] = None
        no_body_issue["current_hash"] = "no_body_hash"

        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=no_body_issue)
            ),
            post_responses=[httpx.Response(status_code=200)],
        )

        await eval_client.run_eval_loop(**DEFAULT_KWARGS)

        call_kwargs = patched_runtime["evaluator"].summarize.call_args.kwargs
        assert call_kwargs["body"] is None

    @pytest.mark.asyncio
    async def test_age_and_activity_days_computed_from_dates(
        self, monkeypatch, patched_runtime
    ) -> None:
        """Age days are computed from the created_at date."""
        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=REAL_ISSUE_SNAPCRAFT)
            ),
            post_responses=[httpx.Response(status_code=200)],
        )

        await eval_client.run_eval_loop(**DEFAULT_KWARGS)

        call_kwargs = patched_runtime["evaluator"].summarize.call_args.kwargs
        # REAL_ISSUE_SNAPCRAFT created_at = 2026-04-01, so age should be ~75 days
        assert call_kwargs["age_days"] >= 70
        assert call_kwargs["age_days"] <= 80

    @pytest.mark.asyncio
    async def test_run_continues_after_failed_submission(
        self, monkeypatch, patched_runtime
    ) -> None:
        """After a failed submission, the client fetches and evaluates the next issue."""

        async def request_shutdown(seconds: int) -> None:
            eval_client.shutdown_state["requested"] = True

        patched_runtime["sleep"].side_effect = request_shutdown
        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=REAL_ISSUE_SNAPCRAFT),
                httpx.Response(status_code=200, json=REAL_ISSUE_LANDSCAPE),
                httpx.Response(status_code=204),
            ),
            post_responses=[
                _response(
                    422, json={"detail": "scores missing required keys: confidence"}
                ),
                httpx.Response(status_code=200),
            ],
        )

        await eval_client.run_eval_loop(**{**DEFAULT_KWARGS, "limit": 0})

        # Both issues were evaluated despite the first submission failing
        assert patched_runtime["evaluator"].summarize.call_count == 2
        # Only the second issue produced a completion line (first submission failed)
        assert len(patched_runtime["console_prints"]) == 1

    @pytest.mark.asyncio
    async def test_run_produces_correct_per_issue_log_line(
        self, monkeypatch, patched_runtime
    ) -> None:
        """Each issue produces a completion line with the action and token counts."""
        _patch_http(
            monkeypatch,
            get_responses=_with_status(
                httpx.Response(status_code=200, json=REAL_ISSUE_SNAPCRAFT)
            ),
            post_responses=[httpx.Response(status_code=200)],
        )

        await eval_client.run_eval_loop(**DEFAULT_KWARGS)

        # Per-issue completion line is printed via progress.console.print
        assert len(patched_runtime["console_prints"]) == 1
        line = patched_runtime["console_prints"][0]
        assert "snapcraft#2695" in line
        assert "needs_triage" in line
        assert "300 in / 200 out tokens" in line
