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


def build_summary_prompt(
    *,
    title: str,
    body: str | None,
    issue_type: str,
    labels: list[str],
) -> list[dict[str, str]]:
    """Build a prompt for summarizing an issue or PR.

    Args:
        title: Issue/PR title.
        body: Issue/PR body text.
        issue_type: 'issue' or 'pull_request'.
        labels: List of label names.

    Returns:
        List of message dicts for the LLM API.

    """
    type_label = "Pull Request" if issue_type == "pull_request" else "Issue"
    label_str = ", ".join(labels) if labels else "none"

    user_content = (
        f"Type: {type_label}\n"
        f"Title: {title}\n"
        f"Labels: {label_str}\n"
        f"Body:\n{(body or '(no body)')[:3000]}"
    )

    return [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def build_evaluation_prompt(  # noqa: PLR0913
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

    Returns:
        List of message dicts for the LLM API.

    """
    type_label = "Pull Request" if issue_type == "pull_request" else "Issue"
    label_str = ", ".join(labels) if labels else "none"
    extra_scores = (
        _PR_EXTRA_SCORES if issue_type == "pull_request" else _ISSUE_EXTRA_SCORES
    )

    system_content = _EVALUATION_SYSTEM + extra_scores

    user_content = (
        f"Type: {type_label}\n"
        f"Title: {title}\n"
        f"Labels: {label_str}\n"
        f"Author: {author} ({'maintainer' if is_maintainer else 'external contributor'})\n"
        f"Age: {age_days} days\n"
        f"Last activity: {last_activity_days} days ago\n"
        f"Comment count: {comment_count}\n"
        f"Body:\n{(body or '(no body)')[:3000]}"
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]
