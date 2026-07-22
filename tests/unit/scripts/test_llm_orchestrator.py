"""Tests for scripts.llm.orchestrator."""

import asyncio
import io
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from craft_dashboard.llm.evaluator import CURRENT_EVAL_VERSION
from craft_dashboard.llm.exceptions import (
    LLMQuotaError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMValidationError,
)
from craft_dashboard.models.issue import Issue
from rich.console import Console
from scripts.eval_timing import TimingHistory
from scripts.llm import orchestrator
from scripts.llm.checkpoint import EvaluationCheckpoint
from scripts.llm.orchestrator import _evaluate_issues
from scripts.llm.queries import IssueEvaluationTarget


@pytest.fixture(autouse=True)
def _reset_shutdown_state(monkeypatch: pytest.MonkeyPatch):
    # Avoid registering a real SIGINT handler (and leaking shutdown state)
    # across tests; each test starts with a clean slate.
    orchestrator.shutdown_state["requested"] = False
    monkeypatch.setattr(orchestrator.signal, "signal", MagicMock())
    yield
    orchestrator.shutdown_state["requested"] = False


def _make_issue(
    *, issue_id: int, issue_type: str = "issue", state: str = "open"
) -> Issue:
    now = datetime.now(tz=UTC)
    if issue_type == "pull_request":
        metadata = {"merged": True}
    elif state == "closed":
        metadata = {"closing_references": [{"number": 99, "state": "merged"}]}
    else:
        metadata = {}
    return Issue(
        id=issue_id,
        project_id=1,
        source="github",
        external_id=str(issue_id),
        issue_type=issue_type,
        title=f"Issue {issue_id}",
        body="Body",
        state=state,
        author="alice",
        labels=["bug"],
        comments=[{"author": "bob", "body": "Looks good"}],
        metadata_=metadata,
        created_at=now,
        updated_at=now,
        last_fetched_at=now,
    )


def _valid_result(*, issue_hash: str) -> dict:
    return {
        "summary": "Issue still looks actionable because the report has clear details.",
        "suggested_action": "keep_open",
        "suggested_action_reason": "Recent comments keep the report active.",
        "scores": {
            "staleness": 10,
            "complexity": 30,
            "support_request": 5,
            "confidence": 90,
        },
        "tokens_used": 77,
        "prompt_tokens": 30,
        "completion_tokens": 47,
        "issue_data_hash": issue_hash,
    }


def _stub_target_queries(
    monkeypatch: pytest.MonkeyPatch,
    targets: list[IssueEvaluationTarget],
) -> None:
    """Replace the query-layer functions the orchestrator calls with fakes.

    Mirrors ``scripts.llm.queries``'s ``count_issue_evaluation_targets``,
    ``stream_issue_evaluation_targets``, and ``fetch_issue_evaluation_breakdown``
    against the given in-memory ``targets`` list, honoring only
    ``include_issue_ids``/``exclude_issue_ids`` (the orchestrator's own
    checkpoint-resume logic) and ignoring the SQL-level filter kwargs
    (project_filter, open_only, etc.), which is queries.py's own
    responsibility and is covered by test_llm_queries.py instead.
    """

    def _matching_ids(**kwargs: object) -> set[int]:
        ids = {target.issue.id for target in targets}
        include_issue_ids = kwargs.get("include_issue_ids")
        if include_issue_ids is not None:
            ids &= set(include_issue_ids)
        exclude_issue_ids = kwargs.get("exclude_issue_ids")
        if exclude_issue_ids:
            ids -= set(exclude_issue_ids)
        return ids

    async def fake_count(*_args: object, **kwargs: object) -> int:
        return len(_matching_ids(**kwargs))

    async def fake_stream(*_args: object, **kwargs: object):
        matching_ids = _matching_ids(**kwargs)
        for target in targets:
            if target.issue.id in matching_ids:
                yield target

    async def fake_breakdown(
        *_args: object, **kwargs: object
    ) -> dict[tuple[str, str], int]:
        matching_ids = _matching_ids(**kwargs)
        breakdown: dict[tuple[str, str], int] = {}
        for target in targets:
            if target.issue.id not in matching_ids:
                continue
            key = (target.project_name, target.issue.state)
            breakdown[key] = breakdown.get(key, 0) + 1
        return breakdown

    monkeypatch.setattr(
        "scripts.llm.orchestrator.count_issue_evaluation_targets", fake_count
    )
    monkeypatch.setattr(
        "scripts.llm.orchestrator.stream_issue_evaluation_targets", fake_stream
    )
    monkeypatch.setattr(
        "scripts.llm.orchestrator.fetch_issue_evaluation_breakdown", fake_breakdown
    )


class TestEvaluateIssues:
    @pytest.mark.asyncio
    async def test_dry_run_returns_count_without_calling_evaluator(
        self, monkeypatch
    ) -> None:
        targets = [
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=1),
                project_name="snapcraft",
                issue_data_hash=None,
            ),
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=2),
                project_name="snapcraft",
                issue_data_hash=None,
            ),
        ]
        evaluator = SimpleNamespace(evaluate=AsyncMock(), model="eval-model")
        _stub_target_queries(monkeypatch, targets)

        stats = await _evaluate_issues(
            session_factory=object(),
            evaluator=evaluator,
            maintainers={"alice"},
            dry_run=True,
            resume=False,
        )

        assert stats == {
            "evaluated": 0,
            "skipped": 0,
            "errored": 0,
            "total_tokens": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "estimated_cost_usd": 0.0,
            "unpriced_evaluations": 0,
            "would_evaluate": 2,
        }
        evaluator.evaluate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_evaluates_rows_skips_unchanged_and_persists_results(
        self, monkeypatch
    ) -> None:
        targets = [
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=1),
                project_name="charmcraft",
                issue_data_hash="old-hash",
                eval_version=CURRENT_EVAL_VERSION - 1,
            ),
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=2, issue_type="pull_request"),
                project_name="snapcraft",
                issue_data_hash="same-hash",
                eval_version=CURRENT_EVAL_VERSION,
            ),
        ]
        evaluator = SimpleNamespace(
            evaluate=AsyncMock(
                side_effect=[
                    _valid_result(issue_hash="new-hash"),
                    None,
                ]
            ),
            model="eval-model",
        )
        persist_result = AsyncMock()
        _stub_target_queries(monkeypatch, targets)
        monkeypatch.setattr(
            "scripts.llm.orchestrator.store_evaluation_result", persist_result
        )

        stats = await _evaluate_issues(
            session_factory=object(),
            evaluator=evaluator,
            maintainers={"alice"},
            llm_backend="local",
            resume=False,
        )

        assert stats == {
            "evaluated": 1,
            "skipped": 1,
            "errored": 0,
            "total_tokens": 77,
            "total_prompt_tokens": 30,
            "total_completion_tokens": 47,
            "estimated_cost_usd": 0.0,
            "unpriced_evaluations": 1,
        }
        persist_result.assert_awaited_once()
        persisted_kwargs = persist_result.await_args.kwargs
        assert persisted_kwargs["issue_id"] == 1
        assert persisted_kwargs["model"] == "eval-model"
        assert persisted_kwargs["llm_backend"] == "local"

        first_call = evaluator.evaluate.await_args_list[0].kwargs
        assert first_call["existing_hash"] is None
        assert first_call["is_maintainer"] is True
        assert first_call["comment_count"] == 1
        assert first_call["pr_details"] is None

        second_call = evaluator.evaluate.await_args_list[1].kwargs
        assert second_call["existing_hash"] == "same-hash"
        assert second_call["pr_details"] == {"merged": True}
        assert first_call["state"] == "open"
        assert second_call["state"] == "open"

    @pytest.mark.asyncio
    async def test_closed_issue_forwards_closing_references(self, monkeypatch) -> None:
        """Closing PRs recorded in metadata_ are forwarded for closed issues.

        Regression test: unlike the HTTP eval-client path (eval_api.py), the
        direct-DB orchestrator previously never extracted closing_references
        from Issue.metadata_ at all, so closed issues never got their closing
        PR context in the summary prompt.
        """
        closed_issue = _make_issue(issue_id=1, state="closed")
        targets = [
            IssueEvaluationTarget(
                issue=closed_issue,
                project_name="charmcraft",
                issue_data_hash=None,
                eval_version=CURRENT_EVAL_VERSION,
            ),
        ]
        evaluator = SimpleNamespace(
            evaluate=AsyncMock(side_effect=[_valid_result(issue_hash="new-hash")]),
            model="eval-model",
        )
        _stub_target_queries(monkeypatch, targets)
        monkeypatch.setattr(
            "scripts.llm.orchestrator.store_evaluation_result", AsyncMock()
        )

        await _evaluate_issues(
            session_factory=object(),
            evaluator=evaluator,
            maintainers={"alice"},
            llm_backend="local",
            resume=False,
        )

        call_kwargs = evaluator.evaluate.await_args_list[0].kwargs
        assert call_kwargs["closing_references"] == [{"number": 99, "state": "merged"}]
        assert call_kwargs["pr_details"] is None

    @pytest.mark.asyncio
    async def test_accepts_closed_issue_summary_only_results(self, monkeypatch) -> None:
        target = IssueEvaluationTarget(
            issue=_make_issue(issue_id=1),
            project_name="charmcraft",
            issue_data_hash=None,
        )
        target.issue.state = "closed"
        evaluator = SimpleNamespace(
            evaluate=AsyncMock(
                return_value={
                    "summary": "Fixed by the core24 packaging update and closed after confirmation.",
                    "suggested_action": None,
                    "suggested_action_reason": None,
                    "scores": {},
                    "tokens_used": 10,
                    "prompt_tokens": 4,
                    "completion_tokens": 6,
                    "issue_data_hash": "hash-1",
                }
            ),
            model="eval-model",
        )
        _stub_target_queries(monkeypatch, [target])
        store_result = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.orchestrator.store_evaluation_result", store_result
        )

        stats = await _evaluate_issues(
            session_factory=object(),
            evaluator=evaluator,
            maintainers=set(),
            resume=False,
        )

        assert stats == {
            "evaluated": 1,
            "skipped": 0,
            "errored": 0,
            "total_tokens": 10,
            "total_prompt_tokens": 4,
            "total_completion_tokens": 6,
            "estimated_cost_usd": 0.0,
            "unpriced_evaluations": 1,
        }
        assert evaluator.evaluate.await_args.kwargs["state"] == "closed"
        store_result.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_invalid_results_without_storing(
        self, monkeypatch, caplog
    ) -> None:
        target = IssueEvaluationTarget(
            issue=_make_issue(issue_id=1),
            project_name="charmcraft",
            issue_data_hash=None,
        )
        evaluator = SimpleNamespace(
            evaluate=AsyncMock(
                return_value={
                    "summary": "too short",
                    "suggested_action": "keep_open",
                    "suggested_action_reason": "Looks fine",
                    "scores": {
                        "staleness": 10,
                        "complexity": 30,
                        "support_request": 5,
                        "readiness": 90,
                    },
                    "tokens_used": 10,
                    "prompt_tokens": 4,
                    "completion_tokens": 6,
                    "issue_data_hash": "hash-1",
                }
            ),
            model="eval-model",
        )
        _stub_target_queries(monkeypatch, [target])
        store_result = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.orchestrator.store_evaluation_result", store_result
        )

        caplog.set_level("WARNING")
        stats = await _evaluate_issues(
            session_factory=object(),
            evaluator=evaluator,
            maintainers=set(),
            resume=False,
        )

        assert stats == {
            "evaluated": 0,
            "skipped": 0,
            "errored": 1,
            "total_tokens": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "estimated_cost_usd": 0.0,
            "unpriced_evaluations": 0,
        }
        store_result.assert_not_awaited()
        assert "Validation failed for issue" in caplog.text

    @pytest.mark.asyncio
    async def test_strict_validation_stops_the_run(self, monkeypatch) -> None:
        targets = [
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=1),
                project_name="charmcraft",
                issue_data_hash=None,
            ),
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=2),
                project_name="snapcraft",
                issue_data_hash=None,
            ),
        ]
        evaluator = SimpleNamespace(
            evaluate=AsyncMock(
                side_effect=[
                    {
                        **_valid_result(issue_hash="hash-1"),
                        "summary": "too short",
                    },
                    None,
                ]
            ),
            model="eval-model",
        )
        _stub_target_queries(monkeypatch, targets)

        with pytest.raises(LLMValidationError):
            await _evaluate_issues(
                session_factory=object(),
                evaluator=evaluator,
                maintainers=set(),
                strict_validation=True,
                resume=False,
            )

        assert evaluator.evaluate.await_count == 1

    @pytest.mark.asyncio
    async def test_retries_transient_timeout_and_stores_result(
        self, monkeypatch, caplog
    ) -> None:
        target = IssueEvaluationTarget(
            issue=_make_issue(issue_id=1),
            project_name="charmcraft",
            issue_data_hash=None,
        )
        monkeypatch.setattr(
            "scripts.llm.orchestrator._retry_sleep",
            AsyncMock(),
        )
        evaluator = SimpleNamespace(
            evaluate=AsyncMock(
                side_effect=[
                    LLMTimeoutError("timeout"),
                    _valid_result(issue_hash="hash-1"),
                ]
            ),
            model="eval-model",
        )
        _stub_target_queries(monkeypatch, [target])
        store_result = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.orchestrator.store_evaluation_result", store_result
        )

        caplog.set_level("WARNING")
        stats = await _evaluate_issues(
            session_factory=object(),
            evaluator=evaluator,
            maintainers=set(),
            resume=False,
        )

        assert stats == {
            "evaluated": 1,
            "skipped": 0,
            "errored": 0,
            "total_tokens": 77,
            "total_prompt_tokens": 30,
            "total_completion_tokens": 47,
            "estimated_cost_usd": 0.0,
            "unpriced_evaluations": 1,
        }
        assert "Retrying charmcraft#1 after attempt 1/3 in 2.0s" in caplog.text
        assert evaluator.evaluate.await_count == 2
        store_result.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_moves_to_next_issue_after_retry_exhaustion(
        self, monkeypatch
    ) -> None:
        targets = [
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=1),
                project_name="charmcraft",
                issue_data_hash=None,
            ),
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=2),
                project_name="snapcraft",
                issue_data_hash=None,
            ),
        ]
        rate_limit_response = httpx.Response(
            429,
            request=httpx.Request("POST", "https://example.test/llm"),
        )
        monkeypatch.setattr(
            "scripts.llm.orchestrator._retry_sleep",
            AsyncMock(),
        )
        evaluator = SimpleNamespace(
            evaluate=AsyncMock(
                side_effect=[
                    httpx.HTTPStatusError(
                        "rate limited",
                        request=rate_limit_response.request,
                        response=rate_limit_response,
                    ),
                    LLMRateLimitError("slow down"),
                    LLMTimeoutError("timeout"),
                    _valid_result(issue_hash="hash-2"),
                ]
            ),
            model="eval-model",
        )
        _stub_target_queries(monkeypatch, targets)
        store_result = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.orchestrator.store_evaluation_result", store_result
        )

        stats = await _evaluate_issues(
            session_factory=object(),
            evaluator=evaluator,
            maintainers=set(),
            resume=False,
        )

        assert stats == {
            "evaluated": 1,
            "skipped": 0,
            "errored": 1,
            "total_tokens": 77,
            "total_prompt_tokens": 30,
            "total_completion_tokens": 47,
            "estimated_cost_usd": 0.0,
            "unpriced_evaluations": 1,
        }
        assert evaluator.evaluate.await_count == 4
        store_result.assert_awaited_once()
        assert store_result.await_args.kwargs["issue_id"] == 2

    @pytest.mark.asyncio
    async def test_resumes_from_checkpoint_and_clears_it_on_success(
        self, monkeypatch
    ) -> None:
        targets = [
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=1),
                project_name="charmcraft",
                issue_data_hash=None,
            ),
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=2),
                project_name="snapcraft",
                issue_data_hash=None,
            ),
        ]
        evaluator = SimpleNamespace(
            evaluate=AsyncMock(return_value=_valid_result(issue_hash="hash-2")),
            model="eval-model",
        )
        _stub_target_queries(monkeypatch, targets)
        monkeypatch.setattr(
            "scripts.llm.orchestrator.compute_filter_hash",
            lambda **_: "filter-hash",
        )
        monkeypatch.setattr(
            "scripts.llm.orchestrator.load_checkpoint",
            lambda filter_hash: EvaluationCheckpoint(
                filter_hash=filter_hash,
                completed_issue_ids=[1],
                timestamp="2025-01-01T00:00:00+00:00",
            ),
        )
        save_checkpoint = MagicMock()
        clear_checkpoint = MagicMock()
        monkeypatch.setattr("scripts.llm.orchestrator.save_checkpoint", save_checkpoint)
        monkeypatch.setattr(
            "scripts.llm.orchestrator.clear_checkpoint", clear_checkpoint
        )
        store_result = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.orchestrator.store_evaluation_result", store_result
        )

        stats = await _evaluate_issues(
            session_factory=object(),
            evaluator=evaluator,
            maintainers=set(),
            project_filter="snapcraft",
            limit=2,
            open_only=True,
            incomplete=True,
            stale_days=7,
        )

        assert stats == {
            "evaluated": 1,
            "skipped": 1,
            "errored": 0,
            "total_tokens": 77,
            "total_prompt_tokens": 30,
            "total_completion_tokens": 47,
            "estimated_cost_usd": 0.0,
            "unpriced_evaluations": 1,
        }
        assert evaluator.evaluate.await_count == 1
        assert evaluator.evaluate.await_args.kwargs["title"] == "Issue 2"
        save_checkpoint.assert_called_once()
        checkpoint = save_checkpoint.call_args.args[0]
        assert checkpoint.completed_issue_ids == [1, 2]
        clear_checkpoint.assert_called_once()
        store_result.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_resume_ignores_existing_checkpoint(self, monkeypatch) -> None:
        target = IssueEvaluationTarget(
            issue=_make_issue(issue_id=1),
            project_name="charmcraft",
            issue_data_hash=None,
        )
        evaluator = SimpleNamespace(
            evaluate=AsyncMock(return_value=_valid_result(issue_hash="hash-1")),
            model="eval-model",
        )
        _stub_target_queries(monkeypatch, [target])
        load_checkpoint = MagicMock()
        monkeypatch.setattr("scripts.llm.orchestrator.load_checkpoint", load_checkpoint)
        monkeypatch.setattr(
            "scripts.llm.orchestrator.compute_filter_hash",
            lambda **_: "filter-hash",
        )
        monkeypatch.setattr("scripts.llm.orchestrator.save_checkpoint", MagicMock())
        monkeypatch.setattr("scripts.llm.orchestrator.clear_checkpoint", MagicMock())
        monkeypatch.setattr(
            "scripts.llm.orchestrator.store_evaluation_result", AsyncMock()
        )

        stats = await _evaluate_issues(
            session_factory=object(),
            evaluator=evaluator,
            maintainers=set(),
            resume=False,
        )

        assert stats == {
            "evaluated": 1,
            "skipped": 0,
            "errored": 0,
            "total_tokens": 77,
            "total_prompt_tokens": 30,
            "total_completion_tokens": 47,
            "estimated_cost_usd": 0.0,
            "unpriced_evaluations": 1,
        }
        load_checkpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_stops_when_quota_is_exhausted(self, monkeypatch) -> None:
        targets = [
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=1),
                project_name="charmcraft",
                issue_data_hash=None,
            ),
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=2),
                project_name="snapcraft",
                issue_data_hash=None,
            ),
        ]
        evaluator = SimpleNamespace(
            evaluate=AsyncMock(side_effect=[LLMQuotaError("quota"), None]),
            model="eval-model",
        )
        _stub_target_queries(monkeypatch, targets)
        store_result = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.orchestrator.store_evaluation_result", store_result
        )

        stats = await _evaluate_issues(
            session_factory=object(),
            evaluator=evaluator,
            maintainers=set(),
            resume=False,
        )

        assert stats == {
            "evaluated": 0,
            "skipped": 0,
            "errored": 0,
            "total_tokens": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "estimated_cost_usd": 0.0,
            "unpriced_evaluations": 0,
        }
        assert evaluator.evaluate.await_count == 1
        store_result.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_concurrent_quota_errors_stop_cleanly_without_double_counting(
        self, monkeypatch
    ) -> None:
        """Several workers hitting LLMQuotaError near-simultaneously must not
        corrupt stats, double-count, or let evaluation continue past the
        exhaustion point -- `quota_exhausted` is a plain bool flagged from
        multiple concurrent tasks, which is safe under asyncio's
        single-threaded cooperative scheduling but worth pinning down with
        an explicit regression test.
        """
        targets = [
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=issue_id),
                project_name="snapcraft",
                issue_data_hash=None,
            )
            for issue_id in range(1, 11)
        ]
        call_count = 0

        async def _evaluate(**_kwargs) -> dict:
            nonlocal call_count
            call_count += 1
            # Yielding control here lets several concurrent workers all reach
            # "about to raise LLMQuotaError" before any of them has had a
            # chance to observe `quota_exhausted` becoming True.
            await asyncio.sleep(0)
            raise LLMQuotaError("quota exhausted")

        evaluator = SimpleNamespace(
            evaluate=AsyncMock(side_effect=_evaluate), model="m"
        )
        _stub_target_queries(monkeypatch, targets)
        store_result = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.orchestrator.store_evaluation_result", store_result
        )

        stats = await _evaluate_issues(
            session_factory=object(),
            evaluator=evaluator,
            maintainers=set(),
            resume=False,
            concurrency=5,
        )

        assert stats == {
            "evaluated": 0,
            "skipped": 0,
            "errored": 0,
            "total_tokens": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "estimated_cost_usd": 0.0,
            "unpriced_evaluations": 0,
        }
        store_result.assert_not_awaited()
        # Multiple workers race into the quota-exhausted branch concurrently,
        # but the run must still stop well short of evaluating every target.
        assert 0 < call_count < len(targets)

    @pytest.mark.asyncio
    async def test_concurrency_evaluates_all_targets_with_multiple_workers(
        self, monkeypatch
    ) -> None:
        targets = [
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=issue_id),
                project_name="snapcraft",
                issue_data_hash=None,
            )
            for issue_id in range(1, 6)
        ]

        async def _evaluate(**_kwargs) -> dict:
            await asyncio.sleep(0)
            return _valid_result(issue_hash="hash")

        evaluator = SimpleNamespace(
            evaluate=AsyncMock(side_effect=_evaluate), model="m"
        )
        _stub_target_queries(monkeypatch, targets)
        store_result = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.orchestrator.store_evaluation_result", store_result
        )

        stats = await _evaluate_issues(
            session_factory=object(),
            evaluator=evaluator,
            maintainers=set(),
            resume=False,
            concurrency=3,
        )

        assert stats == {
            "evaluated": 5,
            "skipped": 0,
            "errored": 0,
            "total_tokens": 5 * 77,
            "total_prompt_tokens": 5 * 30,
            "total_completion_tokens": 5 * 47,
            "estimated_cost_usd": 0.0,
            "unpriced_evaluations": 5,
        }
        assert evaluator.evaluate.await_count == 5
        assert store_result.await_count == 5
        persisted_issue_ids = {
            call.kwargs["issue_id"] for call in store_result.await_args_list
        }
        assert persisted_issue_ids == {1, 2, 3, 4, 5}

    @pytest.mark.asyncio
    async def test_limit_is_not_exceeded_under_concurrency(self, monkeypatch) -> None:
        """Regression test for a race where --limit could be exceeded.

        With concurrency > 1, workers used to check ``stats["evaluated"] >=
        limit`` and pull a new target under the same lock, but
        ``stats["evaluated"]`` was only incremented after the (slow) LLM
        call finished -- so several workers could all pass the check and
        pull a target before any of them finished, evaluating up to
        (concurrency - 1) more issues than requested. This reproduced with
        limit=5, concurrency=8 against 20 available targets: 12 got
        evaluated instead of 5.
        """
        targets = [
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=issue_id),
                project_name="snapcraft",
                issue_data_hash=None,
            )
            for issue_id in range(1, 21)
        ]

        async def _evaluate(**_kwargs) -> dict:
            # Give every worker a chance to race into _next_target() before
            # any single evaluation "completes" and increments
            # stats["evaluated"].
            await asyncio.sleep(0)
            return _valid_result(issue_hash="hash")

        evaluator = SimpleNamespace(
            evaluate=AsyncMock(side_effect=_evaluate), model="m"
        )
        _stub_target_queries(monkeypatch, targets)
        store_result = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.orchestrator.store_evaluation_result", store_result
        )

        stats = await _evaluate_issues(
            session_factory=object(),
            evaluator=evaluator,
            maintainers=set(),
            resume=False,
            concurrency=8,
            limit=5,
        )

        assert stats["evaluated"] == 5
        assert store_result.await_count == 5

    @pytest.mark.asyncio
    async def test_concurrency_runs_evaluations_in_parallel(self, monkeypatch) -> None:
        targets = [
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=issue_id),
                project_name="snapcraft",
                issue_data_hash=None,
            )
            for issue_id in range(1, 3)
        ]
        in_flight = 0
        max_in_flight = 0
        release = asyncio.Event()

        async def _evaluate(**_kwargs) -> dict:
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await release.wait()
            in_flight -= 1
            return _valid_result(issue_hash="hash")

        evaluator = SimpleNamespace(
            evaluate=AsyncMock(side_effect=_evaluate), model="m"
        )
        _stub_target_queries(monkeypatch, targets)
        monkeypatch.setattr(
            "scripts.llm.orchestrator.store_evaluation_result", AsyncMock()
        )

        async def _unblock_once_both_started() -> None:
            while in_flight < 2:  # noqa: ASYNC110
                await asyncio.sleep(0)
            release.set()

        stats, _ = await asyncio.gather(
            _evaluate_issues(
                session_factory=object(),
                evaluator=evaluator,
                maintainers=set(),
                resume=False,
                concurrency=2,
            ),
            _unblock_once_both_started(),
        )

        assert max_in_flight == 2
        assert stats["evaluated"] == 2

    async def test_console_renders_progress_bar_for_each_outcome(
        self, monkeypatch, tmp_path
    ) -> None:
        """Passing console= advances the progress bar for evaluated/skipped/errored."""
        monkeypatch.setattr(
            "scripts.llm.orchestrator.TimingHistory",
            lambda: TimingHistory(tmp_path / "timing.json"),
        )
        targets = [
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=1),
                project_name="snapcraft",
                issue_data_hash=None,
            ),
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=2),
                project_name="snapcraft",
                issue_data_hash=None,
            ),
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=3),
                project_name="snapcraft",
                issue_data_hash=None,
            ),
        ]
        # issue 1 evaluates successfully, issue 2 is skipped (unchanged
        # content), issue 3 errors out.
        results = iter(
            [
                _valid_result(issue_hash="hash"),
                None,
                RuntimeError("boom"),
            ]
        )

        async def _evaluate(**_kwargs) -> dict | None:
            outcome = next(results)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        evaluator = SimpleNamespace(
            evaluate=AsyncMock(side_effect=_evaluate), model="m"
        )
        _stub_target_queries(monkeypatch, targets)
        monkeypatch.setattr(
            "scripts.llm.orchestrator.store_evaluation_result", AsyncMock()
        )

        console = Console(file=io.StringIO())
        stats = await _evaluate_issues(
            session_factory=object(),
            evaluator=evaluator,
            maintainers=set(),
            resume=False,
            console=console,
        )

        assert stats["evaluated"] == 1
        assert stats["skipped"] == 1
        assert stats["errored"] == 1

    async def test_console_none_skips_progress_bar_entirely(self, monkeypatch) -> None:
        """The default (console=None) path behaves exactly as before."""
        targets = [
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=1),
                project_name="snapcraft",
                issue_data_hash=None,
            )
        ]

        async def _evaluate(**_kwargs) -> dict:
            return _valid_result(issue_hash="hash")

        evaluator = SimpleNamespace(
            evaluate=AsyncMock(side_effect=_evaluate), model="m"
        )
        _stub_target_queries(monkeypatch, targets)
        monkeypatch.setattr(
            "scripts.llm.orchestrator.store_evaluation_result", AsyncMock()
        )

        stats = await _evaluate_issues(
            session_factory=object(),
            evaluator=evaluator,
            maintainers=set(),
            resume=False,
        )

        assert stats["evaluated"] == 1

    @pytest.mark.asyncio
    async def test_shutdown_request_stops_before_remaining_targets(
        self, monkeypatch, caplog
    ) -> None:
        """Simulates Ctrl+C: no new targets are picked up, but the
        already-in-flight evaluation finishes and the checkpoint from it is
        preserved (not cleared), since the run didn't actually complete.
        """
        targets = [
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=issue_id),
                project_name="snapcraft",
                issue_data_hash=None,
            )
            for issue_id in range(1, 4)
        ]

        async def _evaluate(**_kwargs) -> dict:
            # Simulate the signal handler firing mid-run, as if the user
            # pressed Ctrl+C while this issue was being evaluated.
            orchestrator.shutdown_state["requested"] = True
            return _valid_result(issue_hash="hash")

        evaluator = SimpleNamespace(
            evaluate=AsyncMock(side_effect=_evaluate), model="m"
        )
        _stub_target_queries(monkeypatch, targets)
        monkeypatch.setattr(
            "scripts.llm.orchestrator.store_evaluation_result", AsyncMock()
        )
        clear_checkpoint = MagicMock()
        monkeypatch.setattr(
            "scripts.llm.orchestrator.clear_checkpoint", clear_checkpoint
        )

        caplog.set_level("INFO")
        stats = await _evaluate_issues(
            session_factory=object(),
            evaluator=evaluator,
            maintainers=set(),
            resume=True,
            concurrency=1,
        )

        assert stats["evaluated"] == 1
        assert evaluator.evaluate.await_count == 1
        clear_checkpoint.assert_not_called()
        assert "Stopped after Ctrl+C" in caplog.text
        assert "resume from checkpoint" in caplog.text
