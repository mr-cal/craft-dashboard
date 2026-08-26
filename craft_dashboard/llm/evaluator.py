"""Issue and PR evaluator using LLM."""

import json
import logging
from typing import Any, TypedDict

from sqlalchemy import case
from sqlalchemy.sql.elements import ColumnElement

from craft_dashboard.llm.client import LLMClient
from craft_dashboard.llm.content_hash import compute_content_hash
from craft_dashboard.llm.prompts import (
    build_closed_evaluate_prompt,
    build_open_evaluate_prompt,
)
from craft_dashboard.models.issue import Issue

#: Evaluation version produced by the current open-issue/PR *scoring*
#: prompt (build_open_evaluate_prompt). Increment only when a change to
#: that prompt is expected to alter open-item scores/summaries — this
#: re-evaluates just the ~2,269 open items, not the closed-item corpus.
CURRENT_EVAL_VERSION: int = 4

#: Evaluation version produced by the current closed-issue/merged-PR
#: *summary* prompt (build_closed_evaluate_prompt). Versioned
#: independently of CURRENT_EVAL_VERSION so a scoring-prompt change never
#: forces a re-summarization of the much larger (17,206-item) closed
#: corpus, and vice versa.
#:
#: MUST be initialized to 4 — the *same* value CURRENT_EVAL_VERSION
#: currently holds — NOT a fresh 1 — because pre-split production rows for
#: closed items already carry ``eval_version = 4`` and a fresh value would
#: incorrectly re-queue the entire closed corpus on first deploy.
CURRENT_SUMMARY_VERSION: int = 4


def current_version_for_state(state: str) -> int:
    """Return the eval-version constant that applies to an issue's state.

    Open issues/PRs go through the scoring path (CURRENT_EVAL_VERSION);
    everything else (closed issues, merged/closed PRs) goes through the
    summary-only path (CURRENT_SUMMARY_VERSION).
    """
    return CURRENT_EVAL_VERSION if state == "open" else CURRENT_SUMMARY_VERSION


def expected_version_sql_expr() -> ColumnElement[int]:
    """Return the SQL expression for the eval version expected per issue state."""
    return case(
        (Issue.state == "open", CURRENT_EVAL_VERSION),
        else_=CURRENT_SUMMARY_VERSION,
    )


logger = logging.getLogger(__name__)

IssueComment = dict[str, Any]
IssueDetails = dict[str, Any]
ScoreMap = dict[str, int | float]


class ParsedEvaluation(TypedDict, total=False):
    """Parsed JSON payload returned by the evaluation model."""

    summary: str
    scores: ScoreMap
    suggested_action: str
    suggested_action_reason: str


class EvaluationResult(TypedDict):
    """Normalized evaluation payload returned to persistence callers."""

    summary: str
    scores: ScoreMap
    suggested_action: str | None
    suggested_action_reason: str | None
    tokens_used: int
    prompt_tokens: int
    completion_tokens: int
    # Actual billed USD cost reported by the backend for this call, if any
    # (e.g. OpenRouter's ``usage.cost``). None when the backend doesn't
    # report cost (local LLM server), in which case callers estimate cost
    # from the static per-token pricing table instead.
    cost_usd: float | None
    issue_data_hash: str


def _needs_reevaluation(
    existing_hash: str | None,
    current_hash: str,
) -> bool:
    """Check if an issue needs re-evaluation based on content hash.

    Args:
        existing_hash: Hash from the last evaluation, or None if never evaluated.
        current_hash: Hash of the current issue content.

    Returns:
        True if the issue needs re-evaluation.

    """
    if existing_hash is None:
        return True
    return existing_hash != current_hash


def _fix_unescaped_quotes(text: str) -> str:
    """Escape stray double-quotes inside JSON string values.

    Some models emit literal quote characters inside a string value (e.g.
    quoting a term for emphasis) without escaping them, which breaks JSON
    parsing at that point. This walks the text tracking whether we're
    inside a string and, when a ``"`` is found that isn't followed by a
    JSON structural character (``,``, ``}``, ``]``, ``:``, or whitespace
    leading to one of those), treats it as an unescaped quote within the
    string and escapes it instead of treating it as the string terminator.

    Args:
        text: Candidate JSON text (already stripped of code fences/think blocks).

    Returns:
        Text with stray in-string quotes escaped.

    """
    structural_after_quote = set(",}]:")
    result: list[str] = []
    in_string = False
    escape = False
    length = len(text)
    for i, ch in enumerate(text):
        if escape:
            result.append(ch)
            escape = False
            continue
        if ch == "\\":
            result.append(ch)
            escape = True
            continue
        if ch != '"':
            result.append(ch)
            continue
        if not in_string:
            in_string = True
            result.append(ch)
            continue
        # We're inside a string and hit a quote — check what follows it to
        # decide whether this is really the end of the string.
        j = i + 1
        while j < length and text[j] in " \t\r\n":
            j += 1
        if j >= length or text[j] in structural_after_quote:
            in_string = False
            result.append(ch)
        else:
            result.append('\\"')
    return "".join(result)


def _extract_summary_fallback(content: str) -> ParsedEvaluation | None:
    """Salvage a summary value from malformed or truncated JSON.

    Some LLM responses are cut short mid-string (observed with grammar
    -constrained decoding) despite otherwise looking complete, producing
    JSON that no amount of quote-fixing can parse. As a last resort, pull
    the raw text following ``"summary":`` directly out of the response so
    a truncated evaluation still yields a usable (non-empty) summary
    instead of being discarded entirely.

    Args:
        content: Cleaned LLM response content.

    Returns:
        A dict with just the ``summary`` key, or None if no summary text
        could be found.

    """
    import re

    match = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)', content, re.DOTALL)
    if not match:
        return None
    raw = match.group(1)
    # Unescape common JSON escape sequences that may appear in the salvaged text.
    try:
        summary = json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        summary = raw
    summary = summary.strip()
    if not summary:
        return None
    return {"summary": summary}


def _parse_evaluation_response(content: str) -> ParsedEvaluation | None:
    """Parse the LLM evaluation response as JSON.

    Handles responses that may be wrapped in markdown code fences, contain
    surrounding text, include <think>...</think> reasoning blocks from
    thinking models (e.g. Qwen3), contain unescaped quotes inside string
    values, or are truncated mid-string.

    Args:
        content: Raw LLM response content.

    Returns:
        Parsed dict with scores and action, or None if parsing fails.

    """
    import re

    cleaned = content.strip()

    # Strip <think>...</think> reasoning blocks emitted by thinking models.
    # The actual response follows the closing tag.
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()

    candidates = [cleaned]

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", cleaned, re.DOTALL)
    if fence_match:
        candidates.append(fence_match.group(1).strip())

    # Extract first {...} block in case of surrounding text
    brace_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if brace_match:
        candidates.append(brace_match.group(0))

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        # Retry after escaping stray unescaped quotes inside string values.
        try:
            return json.loads(_fix_unescaped_quotes(candidate))
        except json.JSONDecodeError:
            pass

    # Last resort: salvage a summary even from truncated/unparsable JSON.
    fallback = _extract_summary_fallback(cleaned)
    if fallback is not None:
        logger.warning(
            "Recovered summary from malformed LLM JSON response: %s", cleaned[:200]
        )
        return fallback

    logger.warning("Failed to parse LLM evaluation response as JSON: %s", cleaned[:200])
    return None


#: Re-exported for backward compatibility — the implementation now lives in
#: ``craft_dashboard.llm.content_hash`` so it can be shared with the
#: collectors (which set ``Issue.content_hash``) without importing the whole
#: evaluator module.
_compute_content_hash = compute_content_hash


class IssueEvaluator:
    """Evaluates issues and PRs using an LLM."""

    def __init__(
        self,
        client: LLMClient,
        model: str = "google/gemini-flash-1.5",
    ) -> None:
        """Initialize the evaluator.

        Args:
            client: LLM completion client.
            model: Model to use for all evaluation calls.

        """
        self.client = client
        self.model = model

    async def evaluate(
        self,
        *,
        title: str,
        body: str | None,
        issue_type: str,
        state: str,
        labels: list[str],
        age_days: int,
        last_activity_days: int,
        author: str,
        is_maintainer: bool,
        comment_count: int,
        comments: list[IssueComment] | None = None,
        pr_details: IssueDetails | None = None,
        existing_hash: str | None = None,
        closing_references: list[IssueComment] | None = None,
    ) -> EvaluationResult | None:
        """Evaluate a single issue or PR in one LLM call.

        For open issues and PRs, returns summary + scores + action.
        For closed/merged, returns summary only (scores={}, action=None).
        Returns None when the content hash is unchanged (skip re-evaluation).

        Args:
            title: Issue/PR title.
            body: Issue/PR body text.
            issue_type: 'issue' or 'pull_request'.
            state: Current issue or PR state.
            labels: List of label names.
            age_days: Days since creation.
            last_activity_days: Days since last update.
            author: Author username.
            is_maintainer: Whether the author is a project maintainer.
            comment_count: Number of comments.
            comments: Recent comment dicts (optional).
            pr_details: PR review/CI/diff data (optional).
            existing_hash: Content hash from previous evaluation, if any.
            closing_references: PRs or issues that closed this issue (optional).

        Returns:
            EvaluationResult dict, or None if skipped (content unchanged).

        """
        label_names = labels if isinstance(labels, list) else []
        normalized_state = state.lower()
        current_hash = _compute_content_hash(
            title,
            body,
            normalized_state,
            label_names,
            comments=comments,
            pr_details=pr_details,
        )

        if not _needs_reevaluation(existing_hash, current_hash):
            logger.debug("Skipping evaluation (content unchanged): %s", title)
            return None

        logger.debug("Evaluating: %s", title)

        if normalized_state in {"closed", "merged"}:
            messages = build_closed_evaluate_prompt(
                title=title,
                body=body,
                issue_type=issue_type,
                state=normalized_state,
                labels=label_names,
                age_days=age_days,
                last_activity_days=last_activity_days,
                author=author,
                is_maintainer=is_maintainer,
                comment_count=comment_count,
                comments=comments,
                closing_references=closing_references,
                pr_details=pr_details,
            )
        else:
            messages = build_open_evaluate_prompt(
                title=title,
                body=body,
                issue_type=issue_type,
                labels=label_names,
                age_days=age_days,
                last_activity_days=last_activity_days,
                author=author,
                is_maintainer=is_maintainer,
                comment_count=comment_count,
                comments=comments,
                pr_details=pr_details,
            )

        response = await self.client.complete(
            model=self.model,
            messages=messages,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        parsed = _parse_evaluation_response(response.content)
        if parsed is None:
            logger.warning("Could not parse evaluation response for: %s", title)

        summary = (parsed.get("summary") if parsed else None) or ""

        if normalized_state in {"closed", "merged"}:
            return {
                "summary": summary,
                "scores": {},
                "suggested_action": None,
                "suggested_action_reason": None,
                "tokens_used": response.total_tokens,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "cost_usd": response.cost_usd,
                "issue_data_hash": current_hash,
            }

        scores = (parsed.get("scores") if parsed else None) or {}
        if "confidence" not in scores:
            scores["confidence"] = 50

        return {
            "summary": summary,
            "scores": scores,
            "suggested_action": parsed.get("suggested_action") if parsed else None,
            "suggested_action_reason": parsed.get("suggested_action_reason")
            if parsed
            else None,
            "tokens_used": response.total_tokens,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "cost_usd": response.cost_usd,
            "issue_data_hash": current_hash,
        }
