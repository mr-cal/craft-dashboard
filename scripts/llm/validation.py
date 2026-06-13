"""Validation helpers for LLM issue evaluation results."""

from __future__ import annotations

from numbers import Real
from typing import Final

from craft_dashboard.llm.exceptions import LLMValidationError

ALLOWED_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "close_stale",
        "close_not_a_bug",
        "needs_triage",
        "needs_review",
        "keep_open",
    }
)
_REQUIRED_SCORE_KEYS: Final[dict[str, frozenset[str]]] = {
    "issue": frozenset({"staleness", "complexity", "support_request", "confidence"}),
    "pull_request": frozenset({"staleness", "complexity", "confidence"}),
}
_MIN_SUMMARY_LENGTH: Final[int] = 20
_MAX_SCORE: Final[int] = 100


def _require_non_empty_string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        msg = f"{field_name} must be a non-empty string"
        raise LLMValidationError(msg)
    return value.strip()


def validate_evaluation_result(
    result: dict[str, object], *, issue_type: str, state: str = "open"
) -> None:
    """Validate a normalized evaluation result before it is persisted."""
    summary = _require_non_empty_string(result.get("summary"), field_name="Summary")
    if len(summary) < _MIN_SUMMARY_LENGTH:
        msg = f"Summary must be at least {_MIN_SUMMARY_LENGTH} characters long"
        raise LLMValidationError(msg)

    scores = result.get("scores")
    if not isinstance(scores, dict):
        msg = "scores must be a mapping"
        raise LLMValidationError(msg)

    if state in {"closed", "merged"}:
        if scores:
            msg = "scores must be empty for closed issues"
            raise LLMValidationError(msg)
        if result.get("suggested_action") is not None:
            msg = "suggested_action must be None for closed issues"
            raise LLMValidationError(msg)
        if result.get("suggested_action_reason") is not None:
            msg = "suggested_action_reason must be None for closed issues"
            raise LLMValidationError(msg)
        return

    required_score_keys = _REQUIRED_SCORE_KEYS.get(issue_type)
    if required_score_keys is None:
        msg = f"Unsupported issue_type for validation: {issue_type}"
        raise LLMValidationError(msg)

    missing_keys = required_score_keys.difference(scores)
    if missing_keys:
        missing_str = ", ".join(sorted(missing_keys))
        msg = f"scores missing required keys: {missing_str}"
        raise LLMValidationError(msg)

    for score_name, score_value in scores.items():
        if isinstance(score_value, bool) or not isinstance(score_value, Real):
            msg = f"score '{score_name}' must be numeric"
            raise LLMValidationError(msg)
        if not 0 <= score_value <= _MAX_SCORE:
            msg = f"score '{score_name}' must be between 0-{_MAX_SCORE}"
            raise LLMValidationError(msg)

    suggested_action = _require_non_empty_string(
        result.get("suggested_action"), field_name="suggested_action"
    )
    if suggested_action not in ALLOWED_ACTIONS:
        msg = f"suggested_action must be one of: {', '.join(sorted(ALLOWED_ACTIONS))}"
        raise LLMValidationError(msg)

    _require_non_empty_string(
        result.get("suggested_action_reason"), field_name="suggested_action_reason"
    )
