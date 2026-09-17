"""Tests for summary-only evaluation of closed issues and merged PRs."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from craft_dashboard.llm.client import LLMResponse
from craft_dashboard.llm.evaluator import IssueEvaluator


@pytest.mark.asyncio
async def test_closed_issue_uses_summary_only_evaluation() -> None:
    """Closed issues return summary with empty scores and no action."""
    mock_response = LLMResponse(
        content='{"summary": "Fixed by the core24 migration patch and closed after confirmation."}',
        total_tokens=15,
        prompt_tokens=10,
        completion_tokens=5,
        model="test-model",
    )
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=mock_response)
    evaluator = IssueEvaluator(
        client=mock_client,
        model_summary="test-model",
        model_scoring="test-model",
    )

    with patch(
        "craft_dashboard.llm.evaluator.build_closed_evaluate_prompt"
    ) as mock_closed:
        mock_closed.return_value = [{"role": "user", "content": "test"}]

        result = await evaluator.evaluate(
            title="snapcraft pack fails with LXD backend on Ubuntu 24.04",
            body="When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
            issue_type="issue",
            state="closed",
            labels=["bug", "priority-high"],
            age_days=45,
            last_activity_days=3,
            author="jdoe-canonical",
            is_maintainer=False,
            comment_count=1,
            comments=[
                {
                    "author": "craft-contributor",
                    "body": "Confirmed fixed in the latest edge build.",
                    "created_at": "2024-01-01T00:00:00+00:00",
                    "type": "comment",
                }
            ],
        )

    assert result is not None
    assert (
        result["summary"]
        == "Fixed by the core24 migration patch and closed after confirmation."
    )
    assert result["scores"] == {}
    assert result["suggested_action"] is None
    assert result["suggested_action_reason"] is None
    mock_closed.assert_called_once()
    assert mock_closed.call_args.kwargs["state"] == "closed"
    # single LLM call
    mock_client.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_closed_issue_returns_action_and_related_work() -> None:
    """Closed issues preserve suggested_action, reason, and related_work."""
    mock_response = LLMResponse(
        content=(
            '{"summary": "Fixed by core24 patch.", '
            '"suggested_action": "closed_resolved", '
            '"suggested_action_reason": "Resolved in PR #3629.", '
            '"related_work": [{"kind": "likely_fixed_by", "ref": "snapcraft#3629", "confidence": 95, "note": "closing PR"}]}'
        ),
        total_tokens=25,
        prompt_tokens=15,
        completion_tokens=10,
        model="test-model",
    )
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=mock_response)
    evaluator = IssueEvaluator(
        client=mock_client,
        model_summary="test-model",
        model_scoring="test-model",
    )

    result = await evaluator.evaluate(
        title="snapcraft pack fails with LXD backend",
        body="details",
        issue_type="issue",
        state="closed",
        labels=["bug"],
        age_days=10,
        last_activity_days=1,
        author="alice",
        is_maintainer=False,
        comment_count=1,
    )

    assert result is not None
    assert result["summary"] == "Fixed by core24 patch."
    assert result["suggested_action"] == "closed_resolved"
    assert result["suggested_action_reason"] == "Resolved in PR #3629."
    assert len(result["related_work"]) == 1
    assert result["related_work"][0]["ref"] == "snapcraft#3629"


@pytest.mark.asyncio
async def test_state_change_triggers_reevaluation_with_new_hash() -> None:
    """Changing an issue from open to closed produces a different content hash."""
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(
        return_value=LLMResponse(
            content='{"summary": "Summary text.", "scores": {"impact": 40, "complexity": 20, "actionability": 70, "confidence": 80}, "suggested_action": "keep_open", "suggested_action_reason": "Active."}',
            total_tokens=10,
            prompt_tokens=7,
            completion_tokens=3,
            model="test-model",
        )
    )
    evaluator = IssueEvaluator(
        client=mock_client,
        model_summary="test-model",
        model_scoring="test-model",
    )

    open_hash = await evaluator.evaluate(
        title="Add support for core24 base",
        body="Please add support for `base: core24`.",
        issue_type="issue",
        state="open",
        labels=["enhancement"],
        age_days=10,
        last_activity_days=1,
        author="alice",
        is_maintainer=False,
        comment_count=0,
    )
    closed_hash = await evaluator.evaluate(
        title="Add support for core24 base",
        body="Please add support for `base: core24`.",
        issue_type="issue",
        state="closed",
        labels=["enhancement"],
        age_days=10,
        last_activity_days=1,
        author="alice",
        is_maintainer=False,
        comment_count=0,
        existing_hash=open_hash["issue_data_hash"] if open_hash else None,
    )

    assert open_hash is not None
    assert closed_hash is not None
    assert open_hash["issue_data_hash"] != closed_hash["issue_data_hash"]
