"""Tests for the issue evaluator."""

import json
from unittest.mock import MagicMock

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
