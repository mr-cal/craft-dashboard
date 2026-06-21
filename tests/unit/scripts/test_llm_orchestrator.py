"""Tests for scripts.llm.orchestrator."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from craft_dashboard.llm.exceptions import (
    LLMQuotaError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMValidationError,
)
from craft_dashboard.models.issue import Issue
from scripts.llm.checkpoint import EvaluationCheckpoint
from scripts.llm.orchestrator import _evaluate_issues
from scripts.llm.queries import IssueEvaluationTarget


def _make_issue(*, issue_id: int, issue_type: str = "issue") -> Issue:
    now = datetime.now(tz=UTC)
    return Issue(
        id=issue_id,
        project_id=1,
        source="github",
        external_id=str(issue_id),
        issue_type=issue_type,
        title=f"Issue {issue_id}",
        body="Body",
        state="open",
        author="alice",
        labels=["bug"],
        comments=[{"author": "bob", "body": "Looks good"}],
        metadata_={"merged": True} if issue_type == "pull_request" else {},
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
        evaluator = SimpleNamespace(
            evaluate=AsyncMock(), model="eval-model"
        )
        fetch_targets = AsyncMock(return_value=targets)
        monkeypatch.setattr(
            "scripts.llm.orchestrator.fetch_issue_evaluation_targets", fetch_targets
        )

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
            ),
            IssueEvaluationTarget(
                issue=_make_issue(issue_id=2, issue_type="pull_request"),
                project_name="snapcraft",
                issue_data_hash="same-hash",
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
        monkeypatch.setattr(
            "scripts.llm.orchestrator.fetch_issue_evaluation_targets",
            AsyncMock(return_value=targets),
        )
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

        assert stats == {"evaluated": 1, "skipped": 1, "errored": 0, "total_tokens": 77}
        persist_result.assert_awaited_once()
        persisted_kwargs = persist_result.await_args.kwargs
        assert persisted_kwargs["issue_id"] == 1
        assert persisted_kwargs["model"] == "eval-model"
        assert persisted_kwargs["llm_backend"] == "local"

        first_call = evaluator.evaluate.await_args_list[0].kwargs
        assert first_call["existing_hash"] == "old-hash"
        assert first_call["is_maintainer"] is True
        assert first_call["comment_count"] == 1
        assert first_call["pr_details"] is None

        second_call = evaluator.evaluate.await_args_list[1].kwargs
        assert second_call["existing_hash"] == "same-hash"
        assert second_call["pr_details"] == {"merged": True}
        assert first_call["state"] == "open"
        assert second_call["state"] == "open"

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
        monkeypatch.setattr(
            "scripts.llm.orchestrator.fetch_issue_evaluation_targets",
            AsyncMock(return_value=[target]),
        )
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

        assert stats == {"evaluated": 1, "skipped": 0, "errored": 0, "total_tokens": 10}
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
        monkeypatch.setattr(
            "scripts.llm.orchestrator.fetch_issue_evaluation_targets",
            AsyncMock(return_value=[target]),
        )
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

        assert stats == {"evaluated": 0, "skipped": 0, "errored": 1, "total_tokens": 0}
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
        monkeypatch.setattr(
            "scripts.llm.orchestrator.fetch_issue_evaluation_targets",
            AsyncMock(return_value=targets),
        )

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
        monkeypatch.setattr(
            "scripts.llm.orchestrator.fetch_issue_evaluation_targets",
            AsyncMock(return_value=[target]),
        )
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

        assert stats == {"evaluated": 1, "skipped": 0, "errored": 0, "total_tokens": 77}
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
        monkeypatch.setattr(
            "scripts.llm.orchestrator.fetch_issue_evaluation_targets",
            AsyncMock(return_value=targets),
        )
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

        assert stats == {"evaluated": 1, "skipped": 0, "errored": 1, "total_tokens": 77}
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
        monkeypatch.setattr(
            "scripts.llm.orchestrator.fetch_issue_evaluation_targets",
            AsyncMock(return_value=targets),
        )
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

        assert stats == {"evaluated": 1, "skipped": 1, "errored": 0, "total_tokens": 77}
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
        monkeypatch.setattr(
            "scripts.llm.orchestrator.fetch_issue_evaluation_targets",
            AsyncMock(return_value=[target]),
        )
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

        assert stats == {"evaluated": 1, "skipped": 0, "errored": 0, "total_tokens": 77}
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
        monkeypatch.setattr(
            "scripts.llm.orchestrator.fetch_issue_evaluation_targets",
            AsyncMock(return_value=targets),
        )
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

        assert stats == {"evaluated": 0, "skipped": 0, "errored": 0, "total_tokens": 0}
        assert evaluator.evaluate.await_count == 1
        store_result.assert_not_awaited()
