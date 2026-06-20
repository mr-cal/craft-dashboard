"""Tests for the issue evaluator."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from craft_dashboard.llm.client import LLMResponse
from craft_dashboard.llm.evaluator import (
    IssueEvaluator,
    _compute_content_hash,
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
                "scores": {"staleness": 85},
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

    def test_strips_think_block_before_json(self) -> None:
        """Thinking model <think> block is stripped before parsing JSON."""
        content = (
            "<think>Let me reason about this issue carefully...</think>\n"
            '{"scores": {"staleness": 70}, "suggested_action": "keep_open", "suggested_action_reason": "Recent activity."}'
        )

        result = _parse_evaluation_response(content)

        assert result is not None
        assert result["scores"]["staleness"] == 70

    def test_strips_think_block_with_braces_inside(self) -> None:
        """Braces inside <think> block are not mistaken for JSON."""
        content = (
            "<think>The JSON should look like: {staleness: 99}</think>\n"
            '{"scores": {"staleness": 10}, "suggested_action": "keep_open", "suggested_action_reason": "Fine."}'
        )

        result = _parse_evaluation_response(content)

        assert result is not None
        assert result["scores"]["staleness"] == 10


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
        hash_no_comments = _compute_content_hash(
            "snapcraft pack fails with LXD backend on Ubuntu 24.04",
            "When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
            "open",
            ["bug", "priority-high"],
            comments=[],
        )
        hash_with_comment = _compute_content_hash(
            "snapcraft pack fails with LXD backend on Ubuntu 24.04",
            "When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
            "open",
            ["bug", "priority-high"],
            comments=[
                {
                    "author": "jdoe-canonical",
                    "body": "I can still reproduce this on Ubuntu 24.04 with `snapcraft pack --use-lxd`.",
                    "created_at": "2024-01-01T00:00:00+00:00",
                    "type": "comment",
                }
            ],
        )

        assert hash_no_comments != hash_with_comment

    def test_same_comments_same_hash(self) -> None:
        """Same comments produce same hash."""
        comments = [
            {
                "author": "craft-contributor",
                "body": "I can still reproduce this with a clean core24 base.",
                "created_at": "2024-01-01T00:00:00+00:00",
                "type": "comment",
            }
        ]
        hash1 = _compute_content_hash(
            "charmcraft deploy times out on large bundles",
            "Deploying a large bundle stalls while charmcraft waits for the controller response.",
            "open",
            ["needs-triage"],
            comments=comments,
        )
        hash2 = _compute_content_hash(
            "charmcraft deploy times out on large bundles",
            "Deploying a large bundle stalls while charmcraft waits for the controller response.",
            "open",
            ["needs-triage"],
            comments=comments,
        )

        assert hash1 == hash2

    def test_comments_default_empty(self) -> None:
        """Hash is stable when comments kwarg is omitted."""
        h1 = _compute_content_hash(
            "Add support for core24 base",
            "Please add support for `base: core24` so new snaps can target Ubuntu 24.04.",
            "open",
            ["enhancement", "snapcraft"],
        )
        h2 = _compute_content_hash(
            "Add support for core24 base",
            "Please add support for `base: core24` so new snaps can target Ubuntu 24.04.",
            "open",
            ["enhancement", "snapcraft"],
            comments=None,
        )

        assert h1 == h2

    def test_comment_order_does_not_affect_hash(self) -> None:
        """Same comments in different order produce same hash."""
        comments_a = [
            {
                "author": "jdoe-canonical",
                "body": "The failure started after switching this project to core24.",
                "created_at": "2024-01-01T00:00:00+00:00",
                "type": "comment",
            },
            {
                "author": "craft-contributor",
                "body": "I can reproduce the same LXD error on a fresh noble container.",
                "created_at": "2024-01-02T00:00:00+00:00",
                "type": "comment",
            },
        ]
        comments_b = list(reversed(comments_a))

        hash_a = _compute_content_hash(
            "snapcraft pack fails with LXD backend on Ubuntu 24.04",
            "When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
            "open",
            ["bug", "priority-high"],
            comments=comments_a,
        )
        hash_b = _compute_content_hash(
            "snapcraft pack fails with LXD backend on Ubuntu 24.04",
            "When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
            "open",
            ["bug", "priority-high"],
            comments=comments_b,
        )

        assert hash_a == hash_b


class TestEvaluateIssueWithComments:
    """Tests that evaluate_issue passes comments and pr_details to prompt builders."""

    @pytest.mark.asyncio
    async def test_evaluate_passes_comments_to_prompt(self) -> None:
        """Comments from call are forwarded to the prompt builders."""
        mock_summary_response = LLMResponse(
            content="Regression report for snapcraft failing with the LXD backend.",
            total_tokens=15,
            prompt_tokens=10,
            completion_tokens=5,
            model="summary-model",
        )
        mock_eval_response = LLMResponse(
            content='{"scores": {"staleness": 10}, "suggested_action": "keep_open", "suggested_action_reason": "Maintainers are still reproducing the LXD failure."}',
            total_tokens=50,
            prompt_tokens=20,
            completion_tokens=30,
            model="evaluation-model",
        )

        mock_client = MagicMock()
        mock_client.complete = AsyncMock(
            side_effect=[mock_summary_response, mock_eval_response]
        )

        evaluator = IssueEvaluator(
            client=mock_client,
            summary_model="test-sum",
            evaluation_model="test-eval",
        )

        comments = [
            {
                "author": "craft-contributor",
                "body": "Still seeing this on snapcraft 8.4 with the LXD backend.",
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
                title="snapcraft pack fails with LXD backend on Ubuntu 24.04",
                body="When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
                issue_type="issue",
                state="open",
                labels=["bug", "priority-high"],
                age_days=45,
                last_activity_days=3,
                author="jdoe-canonical",
                is_maintainer=False,
                comment_count=1,
                comments=comments,
            )

        mock_sum.assert_called_once()
        call_kwargs = mock_sum.call_args.kwargs
        assert call_kwargs["comments"] == comments
        assert call_kwargs["age_days"] == 45
        assert call_kwargs["last_activity_days"] == 3
        assert call_kwargs["comment_count"] == 1
        assert call_kwargs["author"] == "jdoe-canonical"


class TestSummarizeStripsThinkBlocks:
    """Tests that _summarize strips <think> blocks from model responses."""

    @pytest.mark.asyncio
    async def test_strips_think_block_from_summary(self) -> None:
        """<think>...</think> blocks from thinking models are stripped."""
        mock_response = LLMResponse(
            content="<think>reasoning...</think>\nA 10-day-old bug report with no activity.",
            total_tokens=30,
            prompt_tokens=15,
            completion_tokens=15,
            model="summary-model",
        )
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=mock_response)

        evaluator = IssueEvaluator(
            client=mock_client,
            summary_model="test-sum",
            evaluation_model="test-eval",
        )

        summary, _, _, _ = await evaluator.summarize(
            title="snapcraft pack fails with LXD backend on Ubuntu 24.04",
            body="When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
            issue_type="issue",
            state="open",
            labels=["bug", "priority-high"],
            age_days=120,
            last_activity_days=45,
            author="craft-contributor",
            is_maintainer=False,
            comment_count=0,
        )

        assert "<think>" not in summary
        assert "reasoning" not in summary
        assert "10-day-old bug report" in summary

    @pytest.mark.asyncio
    async def test_summary_with_only_think_block_returns_empty(self) -> None:
        """When the entire response is a think block, the summary is empty."""
        mock_response = LLMResponse(
            content="<think>I ran out of tokens while reasoning...</think>",
            total_tokens=30,
            prompt_tokens=15,
            completion_tokens=15,
            model="summary-model",
        )
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=mock_response)

        evaluator = IssueEvaluator(
            client=mock_client,
            summary_model="test-sum",
            evaluation_model="test-eval",
        )

        summary, _, _, _ = await evaluator.summarize(
            title="charmcraft deploy times out on large bundles",
            body="Deploying a large bundle stalls while charmcraft waits for the controller response.",
            issue_type="issue",
            state="open",
            labels=["needs-triage"],
            age_days=45,
            last_activity_days=12,
            author="craft-contributor",
            is_maintainer=False,
            comment_count=0,
        )

        assert summary == ""
