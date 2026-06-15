"""Prompt templates for LLM evaluation of issues and PRs."""

_SUMMARY_SYSTEM = (
    "You are a concise technical writer. Write a brief explanation of at most "
    "256 characters summarising the following GitHub issue or pull request. "
    "Focus on what it is about and its current state (e.g. under discussion, "
    "needs triage, waiting to be assigned). "
    "Do not include markdown formatting. "
    "Do not start with boilerplate like 'This issue', 'This pull request', "
    "'This PR', or 'The issue'. Get straight to the point."
)

_CLOSED_SUMMARY_SYSTEM = (
    "You are a concise technical writer. Write a brief explanation of at most "
    "256 characters summarising what happened with this closed GitHub issue or "
    "pull request. Focus on the outcome: was it fixed, merged, rejected, "
    "superseded, or abandoned? Mention any resolution or merge details. "
    "Do not include markdown formatting. "
    "Do not start with boilerplate like 'This issue', 'This pull request', "
    "'This PR', or 'The issue'. Get straight to the point."
)

_ISSUE_EVAL_SYSTEM = """
You are an expert open-source project maintainer. Evaluate the following \
GitHub issue and provide scores and a suggested action.

Respond with valid JSON matching this schema:
{
  "scores": {
    "staleness": <0-100, how stale/inactive is this>,
    "complexity": <0-100, how complex is this>,
    "support_request": <0-100, how much this is a support request vs actual bug>,
    "confidence": <0-100, how confident you are in the suggested action>
  },
  "suggested_action": "<one of: close_stale, close_not_a_bug, needs_triage, keep_open>",
  "suggested_action_reason": "<1-3 sentences justifying the suggested action and scores>"
}

Score guidelines:

- staleness: 0 = very active, 100 = completely dead. Consider the pace of open-source \
projects:
- {0-30}: Fresh. Under discussion, activity in the last month, or recently referenced \
 in other issues or PRs.
- {31-60}: Moderately stale. Activity within the last 6 months, some acknowledgement \
that this issue is relevant, even if it's not able to be solved (i.e. it requires \
architectural decisions or is blocked by an external dependency).
- {61-100}: Very stale. No activity within the last year or limited maintainer \
interaction. Clearly no longer relevant to a newer version of the project.

- complexity: 0 = trivial, 100 = extremely complex. Issues that would require \
architectural decisions or backward compatibility considerations are more \
complex. Issues that are difficult to reproduce or don't have a simple reproducer \
are also more complex.

- support_request: 0 = actual bug or feature, 100 = support or help request with \
using the tool

- confidence: 0 = not confident the chosen action is correct, 100 = high confidence \
the action is the correct action. High confidence means the issue is clearly one of \
the allowed actions based on the evidence. Low confidence means the issue is \
ambiguous, mixed signals, or would benefit from human review before deciding. You \
should be skeptical and considerate, not overly confident without concrete evidence.

Action guidelines — choose the MOST appropriate action:

- needs_triage: The issue has NOT yet been assessed by a maintainer. Use \
this when the issue lacks labels, has no maintainer response or comments, \
has no assignee, or otherwise shows no sign of having been categorised or \
prioritized. This is the default action for new, unlabelled issues \
regardless of how well-written or actionable they are. A well-structured \
bug report with clear reproduction steps still needs triage if no \
maintainer has acknowledged, labelled, or responded to it yet.

- keep_open: The issue is triaged, valid, and should remain open. Use when \
the issue is clearly scoped, has maintainer buy-in, or is actively being \
worked on. As long as a maintainer has triaged and acknowledged an issue,
it should be kept open unless it's outdated. This is also appropriate if a \
maintainer has asked for further information or changes and has been waiting \
less than 6 months for a response from the original author.

- close_stale: The issue is both inactive AND has become irrelevant. Inactivity alone \
is NEVER a sufficient reason to close an issue. The issue must also show clear signs \
that it is no longer applicable. Valid reasons include: the feature was implemented \
elsewhere, the affected version is no longer supported, or the original problem is no \
longer reproducible. Lack of maintainer engagement is not a sufficient reason alone. \
However, if the maintainer has asked for more details and the reporter has not \
provided details within 6 months, this may be sufficient. Always provide a concrete, \
specific reason why the issue is no longer relevant beyond just its age or inactivity.

- close_not_a_bug: The reported behaviour is working as intended, is a \
support/usage question rather than a bug, or has been resolved through \
configuration or documentation.
"""

_PR_EVAL_SYSTEM = """
You are an expert open-source project maintainer. Evaluate the following \
GitHub pull request and provide scores and a suggested action.

Respond with valid JSON matching this schema:
{
  "scores": {
    "staleness": <0-100, how stale/inactive is this>,
    "complexity": <0-100, how complex is this>,
    "confidence": <0-100, how confident you are in the suggested action>
  },
  "suggested_action": "<one of: close_stale, close_not_mergeable, needs_review, keep_open>",
  "suggested_action_reason": "<1-3 sentences justifying the suggested action and scores>"
}

Score guidelines:

- staleness: 0 = very active, 100 = completely dead. Consider the pace of open-source \
projects:
- {0-30}: Fresh. Under discussion, activity in the last month, or recently referenced \
 in other issues or PRs.
- {31-60}: Moderately stale. Activity within the last 6 months, some acknowledgement \
that this issue is relevant, even if it's not able to be solved (i.e. it requires \
architectural decisions or is blocked by an external dependency).
- {61-100}: Very stale. No activity within the last year or limited maintainer \
interaction. Targets an outdated version of the project. Superseded by a newer PR.

- complexity: 0 = trivial, 100 = extremely complex. PRs that make architectural \
changes or have backward compatibility considerations are more complex. PRs that \
fix difficult to reproduce issues, impact on existing projects is difficult to
reason about, or have extensive integration testing are also more complex.

- confidence: 0 = not confident the chosen action is correct, 100 = high confidence \
the action is the correct action. High confidence means the issue is clearly one of \
the allowed actions based on the evidence. Low confidence means the PR is ambiguous, \
mixed signals, or would benefit from human review before deciding. You should be \
skeptical and considerate, not overly confident without concrete evidence.

Action guidelines — choose the MOST appropriate action:

- needs_review: The PR needs a maintainer review. This may also be appropriate if \
a maintainer has already reviewed a PR and the author has made subsequent changes \
or has asked a question of the maintainer. This is also used when a PR has sufficient \
(2) approvals and is ready to be landed.

- keep_open: The PR has been acknowledged by a maintainer, is under development, \
or is undergoing review cycles. Also use when the PR is a draft PR or is currently \
being developed. As long as it has received updates within 3 months, it can be kept \
open. PRs blocked by other PRs or issues, pending external decisions, or upstream \
changes should be kept open, regardless of their last update.

- close_stale: The PR is both inactive AND has become irrelevant. Inactivity alone \
is NEVER a sufficient reason to close a PR. The PR must also show clear signs \
that it is no longer applicable. Valid reasons include: the feature was implemented \
elsewhere, the affected version is no longer supported, or the original problem it \
solves is no longer reproducible. Lack of maintainer engagement is not a sufficient \
reason alone and neither is failing CI jobs. However, if the maintainer has asked \
for feedback and the reporter has not addressed the feedback within 3 months, this \
may be sufficient. Always provide a concrete, specific reason why the PR is no longer \
relevant beyond just its age or inactivity.

- close_not_mergeable: The PR makes a change that isn't appropriate or relevant, makes \
a backward incompatible change that isn't acceptable, or otherwise makes a change \
that isn't mergeable. This can include adding features or fixing bugs that the \
maintainers expressly state they can't accept.
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


def _build_summary_user_content(
    *,
    title: str,
    body: str | None,
    issue_type: str,
    labels: list[str],
    age_days: int,
    last_activity_days: int,
    comment_count: int,
    author: str,
    is_maintainer: bool,
    comments: list[dict] | None,
    state: str | None = None,
) -> str:
    """Build shared user content for summary prompts."""
    type_label = "Pull Request" if issue_type == "pull_request" else "Issue"
    label_str = ", ".join(labels) if labels else "none"
    comments_text = _format_comments(comments or [])
    state_line = f"State: {state}\n" if state else ""

    return (
        f"Type: {type_label}\n"
        f"{state_line}"
        f"Title: {title}\n"
        f"Labels: {label_str}\n"
        f"Author: {author} ({'maintainer' if is_maintainer else 'external contributor'})\n"
        f"Age: {age_days} days\n"
        f"Last activity: {last_activity_days} days ago\n"
        f"Comment count: {comment_count}\n"
        f"Body:\n{(body or '(no body)')[:3000]}"
        f"{comments_text}"
    )


def build_summary_prompt(
    *,
    title: str,
    body: str | None,
    issue_type: str,
    labels: list[str],
    age_days: int = 0,
    last_activity_days: int = 0,
    comment_count: int = 0,
    author: str = "unknown",
    is_maintainer: bool = False,
    comments: list[dict] | None = None,
) -> list[dict[str, str]]:
    """Build a prompt for summarizing an issue or PR."""
    user_content = _build_summary_user_content(
        title=title,
        body=body,
        issue_type=issue_type,
        labels=labels,
        age_days=age_days,
        last_activity_days=last_activity_days,
        comment_count=comment_count,
        author=author,
        is_maintainer=is_maintainer,
        comments=comments,
    )

    return [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def build_closed_summary_prompt(
    *,
    title: str,
    body: str | None,
    issue_type: str,
    state: str,
    labels: list[str],
    age_days: int = 0,
    last_activity_days: int = 0,
    comment_count: int = 0,
    author: str = "unknown",
    is_maintainer: bool = False,
    comments: list[dict] | None = None,
) -> list[dict[str, str]]:
    """Build a prompt for summarizing a closed issue or merged PR."""
    user_content = _build_summary_user_content(
        title=title,
        body=body,
        issue_type=issue_type,
        state=state,
        labels=labels,
        age_days=age_days,
        last_activity_days=last_activity_days,
        comment_count=comment_count,
        author=author,
        is_maintainer=is_maintainer,
        comments=comments,
    )

    return [
        {"role": "system", "content": _CLOSED_SUMMARY_SYSTEM},
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
    system_content = (
        _PR_EVAL_SYSTEM if issue_type == "pull_request" else _ISSUE_EVAL_SYSTEM
    )

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


# ---------------------------------------------------------------------------
# Phase 2: Duplicate detection prompts
# ---------------------------------------------------------------------------

_DUPLICATE_CHECK_SYSTEM = """\
You are an expert open-source project maintainer. Given two issues or pull \
requests (possibly from different projects), determine whether they describe \
the same underlying problem or feature request.

Two issues are duplicates if they describe the same root cause, bug, or \
feature — even if the symptoms, wording, or reproduction steps differ. \
Cross-project duplicates are common: a feature request in an application and \
a related issue in the underlying library it depends on may be duplicates.

Two issues are NOT duplicates if they merely involve the same component or \
area of the codebase but describe distinct problems.

Respond with valid JSON:
{
  "is_duplicate": <true or false>,
  "confidence": <0-100, how confident you are>,
  "reason": "<brief explanation, one sentence>"
}
"""

_SUMMARY_REWRITE_SYSTEM = """\
You are a concise technical writer. Rewrite the following issue summary to \
note that it is likely a duplicate. Prepend the duplicate reference to a \
condensed version of the original summary. Keep the total under 300 \
characters. Do not include markdown formatting.
"""


def build_duplicate_check_prompt(
    *,
    issue_a_title: str,
    issue_a_summary: str,
    issue_a_project: str,
    issue_b_title: str,
    issue_b_summary: str,
    issue_b_project: str,
    issue_b_external_id: str,
) -> list[dict[str, str]]:
    """Build a prompt to check if two issues are duplicates.

    Accepts cross-project issue pairs; includes project names for context.
    """
    project_note = (
        f" (from {issue_b_project})" if issue_b_project != issue_a_project else ""
    )
    user_content = (
        f"Issue A ({issue_a_project}):\n"
        f"  Title: {issue_a_title}\n"
        f"  Summary: {issue_a_summary}\n\n"
        f"Issue B (#{issue_b_external_id}{project_note}):\n"
        f"  Title: {issue_b_title}\n"
        f"  Summary: {issue_b_summary}\n"
    )
    return [
        {"role": "system", "content": _DUPLICATE_CHECK_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def build_duplicate_summary_rewrite_prompt(
    *,
    original_summary: str,
    duplicate_refs: list[str],
) -> list[dict[str, str]]:
    """Build a prompt to rewrite a summary noting the detected duplicate(s).

    Args:
        original_summary: The original phase-1 summary.
        duplicate_refs: List of human-readable references, e.g. ["snapcraft#123",
            "craft-parts#45"]. Used as-is in the rewrite.

    """
    refs = ", ".join(duplicate_refs)
    user_content = f"Original summary: {original_summary}\nDuplicate of: {refs}\n"
    return [
        {"role": "system", "content": _SUMMARY_REWRITE_SYSTEM},
        {"role": "user", "content": user_content},
    ]
