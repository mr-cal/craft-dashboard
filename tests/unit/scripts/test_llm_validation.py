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
            "duplicateness": 5,
            "complexity": 40,
            "support_request": 15,
            "readiness": 85,
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
        "duplicateness": 5,
        "complexity": 40,
        "readiness": 85,
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
