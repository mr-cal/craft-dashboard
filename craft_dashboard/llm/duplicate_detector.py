"""Phase 2 duplicate detection: embed, search, confirm with LLM, update."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from craft_dashboard.llm.client import LLMClient
    from craft_dashboard.llm.embeddings import EmbeddingClient

from craft_dashboard.llm.prompts import (
    build_duplicate_check_prompt,
    build_duplicate_summary_rewrite_prompt,
)

logger = logging.getLogger(__name__)

# Type for the find_similar function injected by callers
FindSimilarFn = Callable[..., Awaitable[list[dict[str, Any]]]]


class DuplicateDetector:
    """Detect duplicate issues using embeddings + LLM confirmation.

    Phase 2 orchestrator. For a given issue:
    1. Embed the summary.
    2. Find nearest neighbours (candidates) via the injected find_similar_fn.
    3. For each candidate, ask the LLM whether it's a true duplicate.
    4. Return the first confirmed duplicate (or None).

    The caller is responsible for embedding storage and DB updates.
    """

    def __init__(
        self,
        *,
        embedding_client: EmbeddingClient,
        llm_client: LLMClient,
        model: str,
        confidence_threshold: int = 70,
    ) -> None:
        self.embedding_client = embedding_client
        self.llm_client = llm_client
        self.model = model
        self.confidence_threshold = confidence_threshold

    async def check_duplicates(
        self,
        *,
        issue_id: int,
        project_name: str,
        title: str,
        summary: str,
        embedding: list[float],
        find_similar_fn: FindSimilarFn,
    ) -> dict[str, Any] | None:
        """Check if an issue is a duplicate of any existing issue.

        Args:
            issue_id: DB ID of the issue being checked.
            project_name: Project name (for cross-project context in prompts).
            title: Issue title.
            summary: Phase-1 generated summary.
            embedding: Pre-computed embedding for this issue's summary.
            find_similar_fn: Async callable that accepts (embedding, exclude_issue_id)
                and returns a list of candidate dicts with keys:
                issue_id, external_id, project_name, title, summary, distance.

        Returns:
            Dict with duplicate info if confirmed, None otherwise. Keys:
            - duplicate_of_issue_id: int
            - duplicate_of_external_id: str
            - duplicate_of_project_name: str
            - confidence: int (0-100)
            - reason: str
            - candidates_compared: int

        """
        candidates = await find_similar_fn(
            embedding=embedding,
            exclude_issue_id=issue_id,
        )
        candidates_compared = len(candidates)

        for candidate in candidates:
            messages = build_duplicate_check_prompt(
                issue_a_title=title,
                issue_a_summary=summary,
                issue_a_project=project_name,
                issue_b_title=candidate["title"],
                issue_b_summary=candidate["summary"] or "",
                issue_b_project=candidate["project_name"],
                issue_b_external_id=candidate["external_id"],
            )
            try:
                response = await self.llm_client.complete(
                    model=self.model,
                    messages=messages,
                    max_tokens=512,
                    response_format={"type": "json_object"},
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "LLM error checking duplicate against %s#%s, skipping candidate",
                    candidate["project_name"],
                    candidate["external_id"],
                )
                continue

            parsed = _parse_json_response(response.content)
            if (
                parsed
                and parsed.get("is_duplicate")
                and parsed.get("confidence", 0) >= self.confidence_threshold
            ):
                return {
                    "duplicate_of_issue_id": candidate["issue_id"],
                    "duplicate_of_external_id": candidate["external_id"],
                    "duplicate_of_project_name": candidate["project_name"],
                    "confidence": parsed["confidence"],
                    "reason": parsed.get("reason", ""),
                    "candidates_compared": candidates_compared,
                }

        return {"candidates_compared": candidates_compared}

    async def rewrite_summary(
        self,
        *,
        original_summary: str,
        duplicate_refs: list[str],
    ) -> str:
        """Rewrite a summary to note the detected duplicate(s).

        Args:
            original_summary: The original phase-1 summary.
            duplicate_refs: Human-readable refs, e.g. ["snapcraft#123"].

        Returns:
            Rewritten summary string.

        """
        messages = build_duplicate_summary_rewrite_prompt(
            original_summary=original_summary,
            duplicate_refs=duplicate_refs,
        )
        response = await self.llm_client.complete(
            model=self.model,
            messages=messages,
            max_tokens=512,
        )
        return _strip_think_tags(response.content)


def _strip_think_tags(content: str) -> str:
    """Remove <think>...</think> blocks from LLM output (used by reasoning models)."""
    return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()


def _parse_json_response(content: str) -> dict[str, Any] | None:
    """Parse a JSON response, stripping think tags and attempting fallback extraction."""
    cleaned = _strip_think_tags(content)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    brace_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass
    logger.warning("Failed to parse duplicate check response: %.200s", cleaned)
    return None
