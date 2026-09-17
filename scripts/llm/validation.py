"""Validation helpers for LLM issue evaluation results."""

from __future__ import annotations

from numbers import Real
from typing import Final

from craft_dashboard.llm.exceptions import LLMValidationError

# Actions common to both issues and PRs.
_ALL_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "close_resolved",
        "close_superseded",
        "close_stale",
        "close_not_a_bug",
        "close_not_mergeable",
        "needs_triage",
        "needs_review",
        "keep_open",
        "closed_resolved",
        "closed_superseded",
        "closed_not_a_bug",
        "closed_stale",
    }
)

# Actions valid for issue evaluations.
_ISSUE_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "close_resolved",
        "close_stale",
        "close_not_a_bug",
        "needs_triage",
        "needs_review",
        "keep_open",
    }
)

# Actions valid for PR evaluations.
_PR_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "close_superseded",
        "close_stale",
        "close_not_a_bug",
        "close_not_mergeable",
        "needs_review",
        "keep_open",
    }
)

# Actions valid for closed issue/PR evaluations.
_CLOSED_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "closed_resolved",
        "closed_superseded",
        "closed_not_a_bug",
        "closed_stale",
    }
)

_ISSUE_REQUIRED_SCORE_KEYS: Final[frozenset[str]] = frozenset(
    {"impact", "complexity", "actionability", "confidence"}
)
_PR_REQUIRED_SCORE_KEYS: Final[frozenset[str]] = frozenset(
    {"impact", "complexity", "actionability", "confidence"}
)

# Backward-compatible exports.
ALLOWED_ACTIONS: Final[frozenset[str]] = _ALL_ACTIONS
CLOSED_ACTIONS: Final[frozenset[str]] = _CLOSED_ACTIONS
ISSUE_ACTIONS: Final[frozenset[str]] = _ISSUE_ACTIONS
PR_ACTIONS: Final[frozenset[str]] = _PR_ACTIONS

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
        suggested_action = result.get("suggested_action")
        if suggested_action is not None:
            if suggested_action not in _CLOSED_ACTIONS:
                msg = f"suggested_action for closed issue must be one of: {', '.join(sorted(_CLOSED_ACTIONS))}"
                raise LLMValidationError(msg)
            _require_non_empty_string(
                result.get("suggested_action_reason"),
                field_name="suggested_action_reason",
            )
        return

    if issue_type == "pull_request":
        required_score_keys = _PR_REQUIRED_SCORE_KEYS
        valid_actions = _PR_ACTIONS
    else:
        required_score_keys = _ISSUE_REQUIRED_SCORE_KEYS
        valid_actions = _ISSUE_ACTIONS

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
    if suggested_action not in valid_actions:
        msg = f"suggested_action must be one of: {', '.join(sorted(valid_actions))}"
        raise LLMValidationError(msg)

    _require_non_empty_string(
        result.get("suggested_action_reason"), field_name="suggested_action_reason"
    )
