"""Pure helpers for turning a claimed issue and an LLM result into a payload.

Nothing here performs I/O: given the claim dict and the evaluator's result it
derives ages, validates the result, and builds the ``/api/eval/result`` body.
"""

from __future__ import annotations

import logging
import urllib.parse
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from craft_dashboard.llm.evaluator import _compute_content_hash
from craft_dashboard.llm.tool_dispatch import ToolContext

from scripts.llm.validation import validate_evaluation_result

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp into an aware UTC datetime."""
    if not value:
        return None

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def days_since(value: str | None) -> int:
    """Return whole days between ``value`` and now, clamped at zero."""
    timestamp = parse_timestamp(value)
    if timestamp is None:
        return 0
    return max(0, (datetime.now(tz=UTC) - timestamp).days)


def serialize_evidence_paths(ctx: object | None) -> list[dict[str, str]]:
    """Return sorted worker-touched repo/path pairs for `/result` payloads."""
    touched_paths = getattr(ctx, "touched_paths", None) or set()
    return [{"repo": repo, "path": path} for repo, path in sorted(touched_paths)]


def issue_ref_for(issue_data: dict[str, Any]) -> str:
    """Return the ``project#external_id`` label used in every log line."""
    return f"{issue_data['project_name']}#{issue_data['external_id']}"


def warn_on_hash_mismatch(issue_data: dict[str, Any]) -> None:
    """Log a warning when the server's content hash disagrees with ours."""
    local_hash = _compute_content_hash(
        issue_data["title"],
        issue_data.get("body"),
        issue_data["state"],
        issue_data.get("labels", []),
        issue_data.get("comments"),
        pr_details=issue_data.get("pr_details"),
    )
    current_hash = issue_data.get("current_hash", "")
    if current_hash and local_hash != current_hash:
        logger.warning(
            "Issue %s: server hash mismatch (server=%s local=%s)",
            issue_data["external_id"],
            current_hash,
            local_hash,
        )


def build_tool_context(
    issue_data: dict[str, Any],
    *,
    mirror_dir: Path,
    allowed_projects: dict[str, str],
    eval_server_base_url: str,
    headers: dict[str, str],
) -> ToolContext:
    """Build the sandboxed tool context handed to the evaluator."""
    return ToolContext(
        mirror_dir=mirror_dir,
        allowed_projects=allowed_projects,
        pinned_shas=issue_data.get("repo_shas", {}),
        eval_server_base_url=eval_server_base_url,
        eval_api_token=headers.get("Authorization", "").removeprefix("Bearer "),
        issue_id=issue_data["issue_id"],
    )


def validate_result(
    result: dict[str, Any], *, issue_data: dict[str, Any], state: str
) -> None:
    """Validate the scored fields of an LLM result, raising on rejection."""
    validation_payload = {
        "summary": result.get("summary"),
        "scores": result.get("scores", {}),
        "suggested_action": result.get("suggested_action"),
        "suggested_action_reason": result.get("suggested_action_reason"),
    }
    validate_evaluation_result(
        validation_payload,
        issue_type=issue_data.get("issue_type", "issue"),
        state=state,
    )


def build_submission(
    result: dict[str, Any],
    *,
    issue_data: dict[str, Any],
    model: str,
    llm_backend: str,
) -> dict[str, Any]:
    """Build the ``POST /api/eval/result`` body for a completed evaluation."""
    return {
        "issue_id": issue_data["issue_id"],
        "content_hash": result["issue_data_hash"],
        "summary": result["summary"],
        "scores": result["scores"],
        "suggested_action": result["suggested_action"],
        "suggested_action_reason": result["suggested_action_reason"],
        "tokens_used": result["tokens_used"],
        "prompt_tokens": result["prompt_tokens"],
        "completion_tokens": result["completion_tokens"],
        "model_used": model,
        "llm_backend": llm_backend,
        "cost_usd": result["cost_usd"],
        "related_work": result.get("related_work", []),
        "transcript": result.get("transcript"),
        "evidence_paths": serialize_evidence_paths(result.get("tool_context")),
    }


def format_issue_label(
    issue_data: dict[str, Any], *, issue_ref: str, eval_server_base_url: str
) -> str:
    """Return the console label for an issue, hyperlinked when possible."""
    if not eval_server_base_url:
        return f"[bold]{issue_ref}[/bold]"
    quoted_project = urllib.parse.quote(issue_data["project_name"], safe="")
    quoted_id = urllib.parse.quote(str(issue_data["external_id"]), safe="")
    issue_url = (
        f"{eval_server_base_url.rstrip('/')}/issues/{quoted_project}/{quoted_id}"
    )
    return f"[link={issue_url}][bold]{issue_ref}[/bold][/link]"
