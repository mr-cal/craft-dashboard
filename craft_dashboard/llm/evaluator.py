"""Issue and PR evaluator using LLM."""

import hashlib
import json
import logging
from typing import Any, TypedDict

from craft_dashboard.llm.client import LLMClient
from craft_dashboard.llm.prompts import (
    build_closed_summary_prompt,
    build_evaluation_prompt,
    build_summary_prompt,
)

logger = logging.getLogger(__name__)

IssueComment = dict[str, Any]
IssueDetails = dict[str, Any]
ScoreMap = dict[str, int | float]


class ParsedEvaluation(TypedDict, total=False):
    """Parsed JSON payload returned by the evaluation model."""

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


def _parse_evaluation_response(content: str) -> ParsedEvaluation | None:
    """Parse the LLM evaluation response as JSON.

    Handles responses that may be wrapped in markdown code fences, contain
    surrounding text, or include <think>...</think> reasoning blocks from
    thinking models (e.g. Qwen3).

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

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", cleaned, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Extract first {...} block in case of surrounding text
    brace_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse LLM evaluation response as JSON: %s", cleaned[:200])
    return None


def _compute_content_hash(
    title: str,
    body: str | None,
    state: str,
    labels: list[str],
    comments: list[IssueComment] | None = None,
) -> str:
    """Compute a SHA-256 hash of issue content for change detection.

    Includes comments so that new discussion triggers re-evaluation.

    Args:
        title: Issue title.
        body: Issue body text.
        state: Issue state.
        labels: List of label names.
        comments: Recent comments list (optional).

    Returns:
        A 64-character hex string.

    """
    comments_repr = ""
    if comments:
        comments_repr = "|" + ";".join(
            f"{c.get('author', '')}:{c.get('body', '')[:100]}"
            for c in sorted(comments, key=lambda c: c.get("created_at") or "")
        )
    content = f"{title}|{body or ''}|{state}|{','.join(sorted(labels))}{comments_repr}"
    return hashlib.sha256(content.encode()).hexdigest()


class IssueEvaluator:
    """Evaluates issues and PRs using an LLM via OpenRouter."""

    def __init__(
        self,
        client: LLMClient,
        summary_model: str = "google/gemini-flash-1.5",
        evaluation_model: str = "anthropic/claude-sonnet-4-20250514",
    ) -> None:
        """Initialize the evaluator.

        Args:
            client: LLM completion client.
            summary_model: Model to use for summarization (cheaper).
            evaluation_model: Model to use for scoring (more capable).

        """
        self.client = client
        self.summary_model = summary_model
        self.evaluation_model = evaluation_model

    async def summarize(
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
    ) -> tuple[str, int, int, int]:
        """Generate a summary using the cheap model.

        Returns:
            Tuple of (summary text, total tokens, prompt tokens, completion tokens).

        """
        import re

        if state in {"closed", "merged"}:
            summary_messages = build_closed_summary_prompt(
                title=title,
                body=body,
                issue_type=issue_type,
                state=state,
                labels=labels,
                age_days=age_days,
                last_activity_days=last_activity_days,
                author=author,
                is_maintainer=is_maintainer,
                comment_count=comment_count,
                comments=comments,
            )
        else:
            summary_messages = build_summary_prompt(
                title=title,
                body=body,
                issue_type=issue_type,
                labels=labels,
                age_days=age_days,
                last_activity_days=last_activity_days,
                author=author,
                is_maintainer=is_maintainer,
                comment_count=comment_count,
                comments=comments,
            )
        # Allow enough tokens for thinking models (e.g. Qwen3) to reason
        # before producing the actual summary.
        response = await self.client.complete(
            model=self.summary_model,
            messages=summary_messages,
            max_tokens=4096,
        )
        # Strip <think>...</think> reasoning blocks emitted by thinking models.
        content = re.sub(
            r"<think>.*?</think>", "", response.content, flags=re.DOTALL
        ).strip()
        return (
            content,
            response.total_tokens,
            response.prompt_tokens,
            response.completion_tokens,
        )

    async def score(
        self,
        *,
        title: str,
        body: str | None,
        issue_type: str,
        labels: list[str],
        age_days: int,
        last_activity_days: int,
        author: str,
        is_maintainer: bool,
        comment_count: int,
        comments: list[IssueComment] | None = None,
        pr_details: IssueDetails | None = None,
    ) -> tuple[ParsedEvaluation | None, int, int, int]:
        """Score the issue using the more capable model.

        Returns:
            Tuple of (parsed evaluation dict or None, total tokens, prompt tokens, completion tokens).

        """
        eval_messages = build_evaluation_prompt(
            title=title,
            body=body,
            issue_type=issue_type,
            labels=labels,
            age_days=age_days,
            last_activity_days=last_activity_days,
            author=author,
            is_maintainer=is_maintainer,
            comment_count=comment_count,
            comments=comments,
            pr_details=pr_details,
        )
        response = await self.client.complete(
            model=self.evaluation_model,
            messages=eval_messages,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        parsed = _parse_evaluation_response(response.content)
        if parsed is None:
            logger.warning("Could not parse evaluation for: %s", title)
        return (
            parsed,
            response.total_tokens,
            response.prompt_tokens,
            response.completion_tokens,
        )

    async def evaluate_issue(
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
    ) -> EvaluationResult | None:
        """Evaluate a single issue or PR.

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

        Returns:
            Dict with summary, scores, action, tokens, and hash. None if skipped.

        """
        label_names = labels if isinstance(labels, list) else []
        normalized_state = state.lower()
        current_hash = _compute_content_hash(
            title, body, normalized_state, label_names, comments=comments
        )

        if not _needs_reevaluation(existing_hash, current_hash):
            logger.debug("Skipping evaluation (content unchanged): %s", title)
            return None

        logger.debug("Evaluating: %s", title)
        (
            summary,
            summary_tokens,
            summary_prompt,
            summary_completion,
        ) = await self.summarize(
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
        )

        if normalized_state in {"closed", "merged"}:
            return {
                "summary": summary,
                "scores": {},
                "suggested_action": None,
                "suggested_action_reason": None,
                "tokens_used": summary_tokens,
                "prompt_tokens": summary_prompt,
                "completion_tokens": summary_completion,
                "issue_data_hash": current_hash,
            }

        parsed, eval_tokens, eval_prompt, eval_completion = await self.score(
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

        total_tokens = summary_tokens + eval_tokens

        scores = parsed.get("scores", {}) if parsed else {}
        # Default confidence to 50 when the LLM omits it.
        # The prompt asks for confidence, so omission usually means the model
        # skipped it rather than deliberately withholding a value.  50 is
        # a neutral "moderate confidence" default that lets the submission
        # succeed instead of looping forever on the same issue.
        if "confidence" not in scores:
            scores["confidence"] = 50

        return {
            "summary": summary,
            "scores": scores,
            "suggested_action": parsed.get("suggested_action") if parsed else None,
            "suggested_action_reason": parsed.get("suggested_action_reason")
            if parsed
            else None,
            "tokens_used": total_tokens,
            "prompt_tokens": summary_prompt + eval_prompt,
            "completion_tokens": summary_completion + eval_completion,
            "issue_data_hash": current_hash,
        }
