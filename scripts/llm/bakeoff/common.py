"""Shared read-only helpers for the deep-evaluation bake-off scripts."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from craft_dashboard.llm.client import LLMResponse, LocalLLMClient, OpenRouterClient
from craft_dashboard.llm.evaluator import ParsedEvaluation, _parse_evaluation_response
from craft_dashboard.llm.prompts import build_closed_evaluate_prompt
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from sqlalchemy import select

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

STATIC_PRICES: dict[str, tuple[float, float]] = {
    "z-ai/glm-5.2": (1.19, 3.74),
    "qwen/qwen3.8-27b": (0.425, 2.55),
    "deepseek/deepseek-v4-pro-0813": (1.122, 3.366),
    "qwen/qwen3.6-35b-a3b": (0.14, 1.0),
}


@dataclass
class BakeoffResult:
    """Aggregated bake-off output for one issue/model run."""

    issue_ref: str
    model: str
    backend: str
    old_scores: dict[str, Any] = field(default_factory=dict)
    new_output: dict[str, Any] = field(default_factory=dict)
    related_work: list[dict[str, Any]] = field(default_factory=list)
    rounds_used: int = 0
    tools_called: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    wall_seconds: float = 0.0
    completed: bool = False
    error: str | None = None
    sweep_cap: int | None = None


def load_sample(sample_path: Path) -> list[dict[str, str]]:
    """Load a frozen sample fixture."""
    entries = json.loads(sample_path.read_text())
    if not entries:
        raise ValueError(f"Sample file {sample_path} is empty")
    required = {"source", "project", "external_id"}
    for entry in entries:
        missing = required - set(entry)
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise ValueError(f"Sample entry missing required keys: {missing_text}")
    return entries


def make_client(
    *, backend: str, api_key: str = "", base_url: str = "", ca_cert: str = ""
) -> OpenRouterClient | LocalLLMClient:
    """Build the LLM client for the requested backend."""
    if backend == "local":
        return LocalLLMClient(
            base_url=base_url or "http://localhost:11434/v1",
            api_key=api_key,
            ca_cert=ca_cert,
        )
    return OpenRouterClient(api_key=api_key)


def estimate_cost_usd(response: LLMResponse, model: str) -> float | None:
    """Return reported cost or a static estimate."""
    if response.cost_usd is not None:
        return response.cost_usd
    price = STATIC_PRICES.get(model)
    if price is None:
        return None
    prompt_per_mtok, completion_per_mtok = price
    return (
        response.prompt_tokens / 1_000_000 * prompt_per_mtok
        + response.completion_tokens / 1_000_000 * completion_per_mtok
    )


def parse_scoring_output(content: str) -> ParsedEvaluation | None:
    """Parse a model response using the production parser."""
    return _parse_evaluation_response(content)


def _days_since(when: dt.datetime | None) -> int:
    if when is None:
        return 0
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return max((dt.datetime.now(dt.UTC) - when).days, 0)


async def fetch_issue(
    session: AsyncSession, *, source: str, project_name: str, external_id: str
) -> Issue | None:
    """Look up one issue using the real uniqueness key."""
    result = await session.execute(
        select(Issue)
        .join(Project, Issue.project_id == Project.id)
        .where(
            Project.name == project_name,
            Issue.source == source,
            Issue.external_id == external_id,
        )
    )
    return result.scalar_one_or_none()


async def load_old_scores(session: AsyncSession, issue_id: int) -> dict[str, Any]:
    """Load the latest stored production scores for an issue."""
    result = await session.execute(
        select(LLMEvaluation.scores).where(
            LLMEvaluation.issue_id == issue_id,
            LLMEvaluation.latest.is_(True),
        )
    )
    return result.scalar_one_or_none() or {}


def build_summary_messages(issue: Issue) -> list[dict[str, Any]]:
    """Build closed-item summary prompt messages for an issue row."""
    return build_closed_evaluate_prompt(
        title=issue.title,
        body=issue.body,
        issue_type=issue.issue_type,
        state=issue.state,
        labels=issue.labels or [],
        age_days=_days_since(issue.created_at),
        last_activity_days=_days_since(issue.updated_at),
        comment_count=len(issue.comments or []),
        author=issue.author or "unknown",
        is_maintainer=bool(issue.author_is_maintainer),
        comments=issue.comments,
        closing_references=None,
        pr_details=issue.metadata_ or None,
    )
