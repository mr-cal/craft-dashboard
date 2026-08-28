"""Tests for scripts.llm.validation."""

import pytest
from craft_dashboard.llm.exceptions import LLMValidationError
from scripts.llm.validation import validate_evaluation_result


def _valid_result() -> dict:
    return {
        "summary": "Issue remains actionable because the reproducer and scope are clear.",
        "suggested_action": "keep_open",
        "suggested_action_reason": "Maintainers still have enough detail to act on it.",
        "scores": {
            "staleness": 10,
            "complexity": 40,
            "support_request": 15,
            "impact": 30,
            "confidence": 85,
        },
        "tokens_used": 42,
        "prompt_tokens": 20,
        "completion_tokens": 22,
        "issue_data_hash": "hash-1",
    }


def test_accepts_valid_issue_results() -> None:
    validate_evaluation_result(_valid_result(), issue_type="issue")


def test_rejects_boolean_score_values() -> None:
    result = _valid_result()
    result["scores"]["staleness"] = True

    with pytest.raises(LLMValidationError, match="staleness"):
        validate_evaluation_result(result, issue_type="issue")


def test_accepts_valid_pull_request_results() -> None:
    result = _valid_result()
    result["scores"] = {
        "staleness": 10,
        "complexity": 40,
        "impact": 30,
        "confidence": 85,
    }

    validate_evaluation_result(result, issue_type="pull_request")


def test_rejects_missing_required_score_key() -> None:
    result = _valid_result()
    del result["scores"]["support_request"]

    with pytest.raises(LLMValidationError, match="support_request"):
        validate_evaluation_result(result, issue_type="issue")


def test_rejects_non_numeric_score_values() -> None:
    result = _valid_result()
    result["scores"]["staleness"] = "high"

    with pytest.raises(LLMValidationError, match="staleness"):
        validate_evaluation_result(result, issue_type="issue")


def test_rejects_out_of_range_scores() -> None:
    result = _valid_result()
    result["scores"]["staleness"] = 101

    with pytest.raises(LLMValidationError, match="0-100"):
        validate_evaluation_result(result, issue_type="issue")


def test_rejects_short_summary() -> None:
    result = _valid_result()
    result["summary"] = "Too short"

    with pytest.raises(LLMValidationError, match="Summary"):
        validate_evaluation_result(result, issue_type="issue")


def test_rejects_unknown_action() -> None:
    result = _valid_result()
    result["suggested_action"] = "escalate"

    with pytest.raises(LLMValidationError, match="suggested_action"):
        validate_evaluation_result(result, issue_type="issue")


def test_rejects_empty_action_reason() -> None:
    result = _valid_result()
    result["suggested_action_reason"] = ""

    with pytest.raises(LLMValidationError, match="suggested_action_reason"):
        validate_evaluation_result(result, issue_type="issue")


def test_accepts_closed_issue_summary_only_results() -> None:
    result = _valid_result()
    result["scores"] = {}
    result["suggested_action"] = None
    result["suggested_action_reason"] = None

    validate_evaluation_result(result, issue_type="issue", state="closed")


def test_rejects_closed_issue_without_summary() -> None:
    result = _valid_result()
    result["summary"] = "Too short"
    result["scores"] = {}
    result["suggested_action"] = None
    result["suggested_action_reason"] = None

    with pytest.raises(LLMValidationError, match="Summary"):
        validate_evaluation_result(result, issue_type="issue", state="closed")


def test_accepts_close_not_mergeable_for_pr() -> None:
    """PR evaluations may produce close_not_mergeable action."""
    result = _valid_result()
    result["scores"] = {
        "staleness": 30,
        "complexity": 60,
        "impact": 30,
        "confidence": 75,
    }
    result["suggested_action"] = "close_not_mergeable"
    result["suggested_action_reason"] = (
        "The PR introduces a breaking change that maintainers have explicitly "
        "declined to accept."
    )
    validate_evaluation_result(result, issue_type="pull_request")


def test_rejects_close_not_mergeable_for_issue() -> None:
    """close_not_mergeable is invalid for issue evaluations."""
    result = _valid_result()
    result["suggested_action"] = "close_not_mergeable"

    with pytest.raises(LLMValidationError, match="suggested_action"):
        validate_evaluation_result(result, issue_type="issue")


def test_accepts_close_not_a_bug_for_pr() -> None:
    """close_not_a_bug is valid for both issues and PRs."""
    result = _valid_result()
    result["scores"] = {
        "staleness": 30,
        "complexity": 60,
        "impact": 30,
        "confidence": 75,
    }
    result["suggested_action"] = "close_not_a_bug"
    result["suggested_action_reason"] = "The reported behaviour is working as intended."
    validate_evaluation_result(result, issue_type="pull_request")


class TestImpactScoreRequired:
    """impact joins the required score keys for both issues and PRs."""

    def test_issue_missing_impact_raises(self) -> None:
        result = {
            "summary": "Issue remains actionable because the reproducer and scope are clear.",
            "scores": {
                "staleness": 10,
                "complexity": 10,
                "support_request": 10,
                "confidence": 50,
            },
            "suggested_action": "keep_open",
            "suggested_action_reason": "r",
        }
        with pytest.raises(LLMValidationError, match="impact"):
            validate_evaluation_result(result, issue_type="issue", state="open")

    def test_pr_missing_impact_raises(self) -> None:
        result = {
            "summary": "PR remains actionable because the reproducer and scope are clear.",
            "scores": {"staleness": 10, "complexity": 10, "confidence": 50},
            "suggested_action": "keep_open",
            "suggested_action_reason": "r",
        }
        with pytest.raises(LLMValidationError, match="impact"):
            validate_evaluation_result(
                result, issue_type="pull_request", state="open"
            )

    def test_issue_with_impact_passes(self) -> None:
        result = {
            "summary": "Issue remains actionable because the reproducer and scope are clear.",
            "scores": {
                "staleness": 10,
                "complexity": 10,
                "support_request": 10,
                "impact": 40,
                "confidence": 50,
            },
            "suggested_action": "keep_open",
            "suggested_action_reason": "reason long enough for validation",
        }
        validate_evaluation_result(result, issue_type="issue", state="open")
