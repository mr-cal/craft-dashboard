"""Shared factories for test models."""

from datetime import UTC, datetime, timedelta
from typing import Any

from craft_dashboard.llm.content_hash import compute_content_hash
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project


def make_project(
    *,
    name: str = "snapcraft",
    category: str = "application",
    github_org: str = "canonical",
    display_order: int = 1,
    **kwargs: Any,
) -> Project:
    """Build a Project model with sensible defaults for tests."""
    return Project(
        name=name,
        category=category,
        github_org=github_org,
        display_order=display_order,
        **kwargs,
    )


def make_issue(
    *,
    project_id: int = 1,
    source: str = "github",
    external_id: str = "1",
    issue_type: str = "issue",
    title: str = "Test issue",
    body: str = "Test body",
    state: str = "open",
    author: str = "test-user",
    author_is_maintainer: bool = False,
    author_is_bot: bool = False,
    labels: list[str] | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    url: str = "",
    comments: list[dict[str, Any]] | None = None,
    metadata_: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Issue:
    """Build an Issue model with defaults shared across tests."""
    now = datetime.now(tz=UTC)
    resolved_labels = [] if labels is None else labels
    resolved_comments = [] if comments is None else comments
    resolved_metadata = {} if metadata_ is None else metadata_
    kwargs.setdefault(
        "content_hash",
        compute_content_hash(
            title,
            body,
            state,
            resolved_labels,
            resolved_comments,
            pr_details=resolved_metadata or None,
        ),
    )
    return Issue(
        project_id=project_id,
        source=source,
        external_id=external_id,
        issue_type=issue_type,
        title=title,
        body=body,
        state=state,
        author=author,
        author_is_maintainer=author_is_maintainer,
        author_is_bot=author_is_bot,
        labels=resolved_labels,
        created_at=created_at or now - timedelta(days=10),
        updated_at=updated_at or now,
        url=url or f"https://github.com/canonical/test/issues/{external_id}",
        metadata_=resolved_metadata,
        comments=resolved_comments,
        last_fetched_at=now,
        **kwargs,
    )


def make_evaluation(
    *,
    issue_id: int = 1,
    model_name: str = "test-model",
    summary: str = "Test summary",
    suggested_action: str = "needs_triage",
    scores: dict[str, Any] | None = None,
    suggested_action_reason: str = "Test reason",
    tokens_used: int = 10,
    prompt_tokens: int = 6,
    completion_tokens: int = 4,
    llm_backend: str = "test-backend",
    evaluated_at: datetime | None = None,
    issue_data_hash: str | None = None,
    latest: bool = True,
    **kwargs: Any,
) -> LLMEvaluation:
    """Build an LLMEvaluation model with defaults shared across tests."""
    now = datetime.now(tz=UTC)
    return LLMEvaluation(
        issue_id=issue_id,
        model_name=model_name,
        summary=summary,
        suggested_action=suggested_action,
        suggested_action_reason=suggested_action_reason,
        scores={} if scores is None else scores,
        tokens_used=tokens_used,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        llm_backend=llm_backend,
        evaluated_at=evaluated_at or now,
        issue_data_hash=issue_data_hash or f"hash-{issue_id}",
        latest=latest,
        **kwargs,
    )
