"""Prompt templates for LLM evaluation of issues and PRs."""

_SUMMARY_SYSTEM = (
    "You are a concise technical writer. Summarize the following GitHub "
    "issue or pull request in 1-2 sentences. Focus on what the issue is "
    "about and its current status. Do not include markdown formatting."
)

_EVALUATION_SYSTEM = """\
You are an expert open-source project maintainer. Evaluate the following \
GitHub issue or pull request and provide scores and a suggested action.

Respond with valid JSON matching this schema:
{
  "scores": {
    "staleness": <0-100, how stale/inactive is this>,
    "relevance": <0-100, how relevant is this to the project>,
    "duplicateness": <0-100, how likely is this a duplicate>,
    "complexity": <0-100, how complex is this>,
    <additional scores based on type>
  },
  "suggested_action": "<one of: close_stale, close_duplicate, close_not_a_bug, \
close_outdated, needs_triage, needs_review, needs_rebase, keep_open>",
  "suggested_action_reason": "<brief explanation for the suggested action>"
}

Score guidelines:
- staleness: 0 = very active, 100 = completely dead (no activity in months)
- relevance: 0 = not relevant, 100 = critically important
- duplicateness: 0 = unique, 100 = clearly a duplicate
- complexity: 0 = trivial, 100 = extremely complex
"""

_ISSUE_EXTRA_SCORES = """
For issues, also include:
- "support_request": <0-100, how likely this is a support/help request rather than a bug or feature>
"""

_PR_EXTRA_SCORES = """
For pull requests, also include:
- "readiness": <0-100, how ready is this PR for review and merge>
"""


def _format_comments(comments: list[dict]) -> str:
    """Format a list of comment dicts into a readable prompt section.

    Args:
        comments: List of comment dicts with author/body/created_at/type.

    Returns:
        Formatted multi-line string, or empty string if no comments.

    """
    if not comments:
        return ""
    lines = ["\nRecent comments:"]
    for c in comments:
        date = (c.get("created_at") or "")[:10]
        ctype = " [review]" if c.get("type") == "review_comment" else ""
        lines.append(
            f"- @{c.get('author', 'unknown')}{ctype} ({date}): {c.get('body') or '(no comment)'}"
        )
    return "\n".join(lines)


def _format_pr_details(pr_details: dict) -> str:
    """Format PR-specific metadata into a readable prompt section.

    Args:
        pr_details: Dict with review_status, CI lists, diff stats, etc.

    Returns:
        Formatted multi-line string, or empty string if pr_details is empty.

    """
    if not pr_details:
        return ""
    ci_passing = ", ".join(pr_details.get("ci_passing", [])) or "none"
    ci_failing = ", ".join(pr_details.get("ci_failing", [])) or "none"
    ci_pending = ", ".join(pr_details.get("ci_pending", [])) or "none"
    unresolved = pr_details.get("unresolved_review_comments", 0)
    return (
        f"\nReview status: {pr_details.get('review_status', 'pending')}"
        f" ({pr_details.get('review_count', 0)} reviewers)"
        f"\nUnresolved review comments: {unresolved}"
        f"\nCI checks:"
        f"\n  Passing: [{ci_passing}]"
        f"\n  Failing: [{ci_failing}]"
        f"\n  Pending: [{ci_pending}]"
        f"\nDiff: +{pr_details.get('diff_additions', 0)}/-{pr_details.get('diff_deletions', 0)} lines,"
        f" {pr_details.get('diff_files_changed', 0)} files changed"
    )


def build_summary_prompt(
    *,
    title: str,
    body: str | None,
    issue_type: str,
    labels: list[str],
    comments: list[dict] | None = None,
) -> list[dict[str, str]]:
    """Build a prompt for summarizing an issue or PR.

    Args:
        title: Issue/PR title.
        body: Issue/PR body text.
        issue_type: 'issue' or 'pull_request'.
        labels: List of label names.
        comments: Recent comments to include (optional).

    Returns:
        List of message dicts for the LLM API.

    """
    type_label = "Pull Request" if issue_type == "pull_request" else "Issue"
    label_str = ", ".join(labels) if labels else "none"
    comments_text = _format_comments(comments or [])

    user_content = (
        f"Type: {type_label}\n"
        f"Title: {title}\n"
        f"Labels: {label_str}\n"
        f"Body:\n{(body or '(no body)')[:3000]}"
        f"{comments_text}"
    )

    return [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def build_evaluation_prompt(
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
) -> list[dict[str, str]]:
    """Build a prompt for evaluating and scoring an issue or PR.

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
        comments: Recent comment dicts with author/body/created_at/type (optional).
        pr_details: PR-specific data: review_status, CI lists, diff stats (optional).

    Returns:
        List of message dicts for the LLM API.

    """
    type_label = "Pull Request" if issue_type == "pull_request" else "Issue"
    label_str = ", ".join(labels) if labels else "none"
    extra_scores = (
        _PR_EXTRA_SCORES if issue_type == "pull_request" else _ISSUE_EXTRA_SCORES
    )

    system_content = _EVALUATION_SYSTEM + extra_scores

    comments_text = _format_comments(comments or [])
    pr_details_text = (
        _format_pr_details(pr_details or {}) if issue_type == "pull_request" else ""
    )

    user_content = (
        f"Type: {type_label}\n"
        f"Title: {title}\n"
        f"Labels: {label_str}\n"
        f"Author: {author} ({'maintainer' if is_maintainer else 'external contributor'})\n"
        f"Age: {age_days} days\n"
        f"Last activity: {last_activity_days} days ago\n"
        f"Comment count: {comment_count}\n"
        f"Body:\n{(body or '(no body)')[:3000]}"
        f"{pr_details_text}"
        f"{comments_text}"
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]
