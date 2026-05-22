"""Tests for the issue evaluator."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from craft_dashboard.llm.evaluator import (
    IssueEvaluator,
    _needs_reevaluation,
    _parse_evaluation_response,
)


class TestNeedsReevaluation:
    """Tests for _needs_reevaluation."""

    def test_no_existing_evaluation(self) -> None:
        """Issues with no evaluation always need evaluation."""
        assert _needs_reevaluation(existing_hash=None, current_hash="abc123") is True

    def test_hash_changed(self) -> None:
        """Issues with changed content need re-evaluation."""
        assert (
            _needs_reevaluation(existing_hash="old_hash", current_hash="new_hash")
            is True
        )

    def test_hash_unchanged(self) -> None:
        """Issues with unchanged content don't need re-evaluation."""
        assert (
            _needs_reevaluation(existing_hash="same_hash", current_hash="same_hash")
            is False
        )


class TestParseEvaluationResponse:
    """Tests for _parse_evaluation_response."""

    def test_valid_json(self) -> None:
        """Parse valid JSON evaluation response."""
        content = json.dumps(
            {
                "scores": {"staleness": 85, "relevance": 30},
                "suggested_action": "close_stale",
                "suggested_action_reason": "No activity for 6 months.",
            }
        )

        result = _parse_evaluation_response(content)

        assert result["scores"]["staleness"] == 85
        assert result["suggested_action"] == "close_stale"
        assert "No activity" in result["suggested_action_reason"]

    def test_json_wrapped_in_markdown(self) -> None:
        """Parse JSON wrapped in markdown code fences."""
        content = '```json\n{"scores": {"staleness": 50}, "suggested_action": "keep_open", "suggested_action_reason": "Active."}\n```'

        result = _parse_evaluation_response(content)

        assert result["scores"]["staleness"] == 50

    def test_parse_response_extracts_json_from_code_fence(self) -> None:
        """JSON in markdown code fences with surrounding text is extracted."""
        content = 'Here is the result:\n```json\n{"scores": {"staleness": 50}, "suggested_action": "keep_open", "suggested_action_reason": "Active."}\n```\nDone.'
        result = _parse_evaluation_response(content)
        assert result is not None
        assert result["scores"]["staleness"] == 50
        assert result["suggested_action"] == "keep_open"

    def test_invalid_json_returns_none(self) -> None:
        """Invalid JSON returns None."""
        result = _parse_evaluation_response("This is not JSON at all.")

        assert result is None


class TestIssueEvaluator:
    """Tests for IssueEvaluator."""

    def test_init(self) -> None:
        """IssueEvaluator initializes with client and model config."""
        mock_client = MagicMock()
        evaluator = IssueEvaluator(
            client=mock_client,
            summary_model="google/gemini-flash-1.5",
            evaluation_model="anthropic/claude-sonnet-4-20250514",
        )

        assert evaluator.summary_model == "google/gemini-flash-1.5"
        assert evaluator.evaluation_model == "anthropic/claude-sonnet-4-20250514"


class TestComputeContentHash:
    """Tests for _compute_content_hash."""

    def test_new_comment_triggers_reevaluation(self) -> None:
        """Adding a new comment changes the content hash."""
        from craft_dashboard.llm.evaluator import _compute_content_hash

        hash_no_comments = _compute_content_hash(
            "title", "body", "open", ["bug"], comments=[]
        )
        hash_with_comment = _compute_content_hash(
            "title",
            "body",
            "open",
            ["bug"],
            comments=[
                {
                    "author": "alice",
                    "body": "Is this fixed?",
                    "created_at": "2024-01-01T00:00:00+00:00",
                    "type": "comment",
                }
            ],
        )

        assert hash_no_comments != hash_with_comment

    def test_same_comments_same_hash(self) -> None:
        """Same comments produce same hash."""
        from craft_dashboard.llm.evaluator import _compute_content_hash

        comments = [
            {
                "author": "alice",
                "body": "hi",
                "created_at": "2024-01-01T00:00:00+00:00",
                "type": "comment",
            }
        ]
        hash1 = _compute_content_hash("t", "b", "open", [], comments=comments)
        hash2 = _compute_content_hash("t", "b", "open", [], comments=comments)

        assert hash1 == hash2

    def test_comments_default_empty(self) -> None:
        """Hash is stable when comments kwarg is omitted."""
        from craft_dashboard.llm.evaluator import _compute_content_hash

        h1 = _compute_content_hash("t", "b", "open", [])
        h2 = _compute_content_hash("t", "b", "open", [], comments=None)

        assert h1 == h2

    def test_comment_order_does_not_affect_hash(self) -> None:
        """Same comments in different order produce same hash."""
        from craft_dashboard.llm.evaluator import _compute_content_hash

        comments_a = [
            {
                "author": "alice",
                "body": "first",
                "created_at": "2024-01-01T00:00:00+00:00",
                "type": "comment",
            },
            {
                "author": "bob",
                "body": "second",
                "created_at": "2024-01-02T00:00:00+00:00",
                "type": "comment",
            },
        ]
        comments_b = list(reversed(comments_a))

        hash_a = _compute_content_hash("t", "b", "open", [], comments=comments_a)
        hash_b = _compute_content_hash("t", "b", "open", [], comments=comments_b)

        assert hash_a == hash_b


class TestEvaluateIssueWithComments:
    """Tests that evaluate_issue passes comments and pr_details to prompt builders."""

    @pytest.mark.asyncio
    async def test_evaluate_passes_comments_to_prompt(self) -> None:
        """Comments from call are forwarded to the prompt builders."""
        from craft_dashboard.llm.client import OpenRouterResponse

        mock_summary_response = OpenRouterResponse(
            content="A bug report.",
            total_tokens=15,
            prompt_tokens=10,
            completion_tokens=5,
        )
        mock_eval_response = OpenRouterResponse(
            content='{"scores": {"staleness": 10}, "suggested_action": "keep_open", "suggested_action_reason": "Active."}',
            total_tokens=50,
            prompt_tokens=20,
            completion_tokens=30,
        )

        mock_client = MagicMock()
        mock_client.chat = AsyncMock(
            side_effect=[mock_summary_response, mock_eval_response]
        )

        evaluator = IssueEvaluator(
            client=mock_client,
            summary_model="test-sum",
            evaluation_model="test-eval",
        )

        comments = [
            {
                "author": "alice",
                "body": "hi",
                "created_at": "2024-01-01T00:00:00+00:00",
                "type": "comment",
            }
        ]

        with (
            patch("craft_dashboard.llm.evaluator.build_summary_prompt") as mock_sum,
            patch("craft_dashboard.llm.evaluator.build_evaluation_prompt") as mock_eval,
        ):
            mock_sum.return_value = [{"role": "user", "content": "test"}]
            mock_eval.return_value = [{"role": "user", "content": "test"}]

            await evaluator.evaluate_issue(
                title="Bug",
                body="Body",
                issue_type="issue",
                labels=[],
                age_days=5,
                last_activity_days=1,
                author="user",
                is_maintainer=False,
                comment_count=1,
                comments=comments,
            )

        mock_sum.assert_called_once()
        call_kwargs = mock_sum.call_args.kwargs
        assert call_kwargs["comments"] == comments

        mock_eval.assert_called_once()
        eval_kwargs = mock_eval.call_args.kwargs
        assert eval_kwargs["comments"] == comments
