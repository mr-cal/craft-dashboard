"""Mapping of GitHub API payloads onto ``Issue`` column values."""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

from github.Issue import Issue as GHIssue

from craft_dashboard.llm.content_hash import compute_content_hash


def classify_issue(gh_issue: GHIssue) -> tuple[str, str]:
    """Classify a GitHub issue as issue or PR, and determine its state.

    Args:
        gh_issue: A PyGithub Issue object.

    Returns:
        A tuple of (issue_type, state).

    """
    is_pr = gh_issue.pull_request is not None
    issue_type = "pull_request" if is_pr else "issue"

    if gh_issue.state == "closed" and is_pr:
        merged_at = getattr(gh_issue.pull_request, "merged_at", None)
        if merged_at is not None:
            return issue_type, "merged"

    return issue_type, gh_issue.state


def compute_issue_hash(
    title: str,
    body: str | None,
    state: str,
    labels: list[str],
) -> str:
    """Compute a SHA-256 hash of issue content for change detection.

    Args:
        title: Issue title.
        body: Issue body text.
        state: Issue state.
        labels: List of label names.

    Returns:
        A 64-character hex string.

    """
    content = f"{title}|{body or ''}|{state}|{','.join(sorted(labels))}"
    return hashlib.sha256(content.encode()).hexdigest()


def classify_change_type(
    last_fetched: datetime | None,
    state: str,
    previous_closed_at: datetime | None,
) -> str:
    """Classify how an issue changed since the previous collection pass.

    Args:
        last_fetched: When the issue was last fetched, or None if unseen.
        state: The issue's normalized state.
        previous_closed_at: The stored closed_at, if any.

    Returns:
        One of "created", "closed", or "updated".

    """
    # Note: if an issue closes, reopens, and closes again entirely between
    # polls (the reopen never independently observed), previous_closed_at
    # stays stale from the first closure and this reclose is classified as
    # "updated" rather than "closed" — an accepted single-poll-granularity
    # simplification, not a bug.
    if last_fetched is None:
        return "created"
    if state == "closed" and previous_closed_at is None:
        return "closed"
    return "updated"


def build_issue_values(
    gh_issue: GHIssue,
    *,
    project_id: int,
    issue_type: str,
    state: str,
    comments: list,
    extra_metadata: dict,
    is_maintainer: Callable[[str], bool],
    fetched_at: datetime,
) -> dict:
    """Build the values dict for an issue upsert.

    Args:
        gh_issue: A PyGithub Issue object.
        project_id: The database ID of the project.
        issue_type: 'issue' or 'pull_request'.
        state: Normalized state string.
        comments: Fetched comment dicts.
        extra_metadata: PR details or other metadata.
        is_maintainer: Predicate deciding whether an author is a maintainer.
        fetched_at: Timestamp to record as last_fetched_at.

    Returns:
        Dict of column values for the insert statement.

    """
    author = gh_issue.user.login if gh_issue.user else None
    labels = [label.name for label in gh_issue.labels]
    return {
        "project_id": project_id,
        "source": "github",
        "external_id": str(gh_issue.number),
        "issue_type": issue_type,
        "title": gh_issue.title,
        "body": gh_issue.body,
        "state": state,
        "author": author,
        "author_is_maintainer": is_maintainer(author) if author else False,
        "author_is_bot": author.endswith("[bot]") if author else False,
        "labels": labels,
        "created_at": gh_issue.created_at.replace(tzinfo=UTC)
        if gh_issue.created_at
        else None,
        "updated_at": gh_issue.updated_at.replace(tzinfo=UTC)
        if gh_issue.updated_at
        else None,
        "closed_at": gh_issue.closed_at.replace(tzinfo=UTC)
        if gh_issue.closed_at
        else None,
        "url": gh_issue.html_url,
        "metadata_": extra_metadata,
        "comments": comments,
        "content_hash": compute_content_hash(
            gh_issue.title,
            gh_issue.body,
            state,
            labels,
            comments,
            pr_details=extra_metadata or None,
        ),
        "last_fetched_at": fetched_at,
    }
