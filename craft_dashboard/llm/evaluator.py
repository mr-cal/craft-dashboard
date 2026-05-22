"""Issue and PR evaluator using LLM."""

import hashlib
import json
import logging

from craft_dashboard.llm.client import OpenRouterClient
from craft_dashboard.llm.prompts import build_evaluation_prompt, build_summary_prompt

logger = logging.getLogger(__name__)


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


def _parse_evaluation_response(content: str) -> dict | None:
    """Parse the LLM evaluation response as JSON.

    Handles responses that may be wrapped in markdown code fences or contain
    surrounding text.

    Args:
        content: Raw LLM response content.

    Returns:
        Parsed dict with scores and action, or None if parsing fails.

    """
    import re

    cleaned = content.strip()

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
    comments: list[dict] | None = None,
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
        client: OpenRouterClient,
        summary_model: str = "google/gemini-flash-1.5",
        evaluation_model: str = "anthropic/claude-sonnet-4-20250514",
    ) -> None:
        """Initialize the evaluator.

        Args:
            client: OpenRouter HTTP client.
            summary_model: Model to use for summarization (cheaper).
            evaluation_model: Model to use for scoring (more capable).

        """
        self.client = client
        self.summary_model = summary_model
        self.evaluation_model = evaluation_model

    async def _summarize(
        self,
        *,
        title: str,
        body: str | None,
        issue_type: str,
        labels: list[str],
        comments: list[dict] | None = None,
    ) -> tuple[str, int]:
        """Generate a summary using the cheap model.

        Returns:
            Tuple of (summary text, tokens used).

        """
        summary_messages = build_summary_prompt(
            title=title,
            body=body,
            issue_type=issue_type,
            labels=labels,
            comments=comments,
        )
        response = await self.client.chat(
            model=self.summary_model,
            messages=summary_messages,
            max_tokens=256,
        )
        return response.content, response.total_tokens

    async def _score(  # noqa: PLR0913 — LLM evaluation requires many distinct issue attributes
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
        comments: list[dict] | None = None,
        pr_details: dict | None = None,
    ) -> tuple[dict | None, int]:
        """Score the issue using the more capable model.

        Returns:
            Tuple of (parsed evaluation dict or None, tokens used).

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
        response = await self.client.chat(
            model=self.evaluation_model,
            messages=eval_messages,
            max_tokens=512,
            response_format={"type": "json_object"},
        )
        parsed = _parse_evaluation_response(response.content)
        if parsed is None:
            logger.warning("Could not parse evaluation for: %s", title)
        return parsed, response.total_tokens

    async def evaluate_issue(  # noqa: PLR0913 — LLM evaluation requires many distinct issue attributes
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
        comments: list[dict] | None = None,
        pr_details: dict | None = None,
        existing_hash: str | None = None,
    ) -> dict | None:
        """Evaluate a single issue or PR.

        Args:
            title: Issue/PR title.
            body: Issue/PR body text.
            issue_type: 'issue' or 'pull_request'.
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
        current_hash = _compute_content_hash(
            title, body, "open", label_names, comments=comments
        )

        if not _needs_reevaluation(existing_hash, current_hash):
            logger.debug("Skipping evaluation (content unchanged): %s", title)
            return None

        summary, summary_tokens = await self._summarize(
            title=title,
            body=body,
            issue_type=issue_type,
            labels=label_names,
            comments=comments,
        )

        parsed, eval_tokens = await self._score(
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

        return {
            "summary": summary,
            "scores": parsed.get("scores", {}) if parsed else {},
            "suggested_action": parsed.get("suggested_action") if parsed else None,
            "suggested_action_reason": parsed.get("suggested_action_reason")
            if parsed
            else None,
            "tokens_used": total_tokens,
            "issue_data_hash": current_hash,
        }
