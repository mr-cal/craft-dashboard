"""REST fetches against the GitHub API for issue, PR, and tag details."""

from typing import TYPE_CHECKING

from github.Issue import Issue as GHIssue
from github.PullRequest import PullRequest as GHPullRequest

if TYPE_CHECKING:
    from github.Repository import Repository as GHRepository

_RECENT_COMMENT_LIMIT = 10


def tag_on_main(repo: "GHRepository", best_tag: str) -> bool:
    """Return True if best_tag is an ancestor of main (i.e., main contains the tag).

    Uses the GitHub compare API: compare(base=best_tag, head="main").
    If behind_by == 0, main has not diverged behind the tag, meaning the tag's
    commit is reachable from main's history.
    """
    try:
        comparison = repo.compare(best_tag, "main")
    except Exception:  # noqa: BLE001
        return False
    else:
        return comparison.behind_by == 0


def fetch_issue_comments(gh_issue: GHIssue) -> list[dict]:
    """Fetch the last 10 comments from a GitHub issue.

    Args:
        gh_issue: A PyGithub Issue object.

    Returns:
        List of comment dicts, each with author/body/created_at/type.

    """
    comments = list(gh_issue.get_comments())
    recent = comments[-_RECENT_COMMENT_LIMIT:]
    return [
        {
            "author": c.user.login if c.user else "unknown",
            "body": (c.body or "")[:1000],
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "type": "comment",
        }
        for c in recent
    ]


def fetch_closing_references(gh_issue: GHIssue) -> list[dict]:
    """Fetch PRs that closed this issue via GitHub timeline events.

    Args:
        gh_issue: A PyGithub Issue object (closed issues only).

    Returns:
        List of dicts describing each merged PR that closed the issue.

    """
    refs = []
    for event in gh_issue.get_timeline():
        if event.event == "cross-referenced" and event.source:
            source = event.source
            if (
                source.type == "pull_request"
                and source.issue is not None
                and source.issue.pull_request is not None
                and source.issue.pull_request.merged_at is not None
            ):
                refs.append(
                    {
                        "type": "pull_request",
                        "number": source.issue.number,
                        "title": source.issue.title,
                        "url": source.issue.html_url,
                        "state": "merged",
                        "merged_at": source.issue.pull_request.merged_at.isoformat(),
                    }
                )
    return refs


def fetch_pr_details(gh_pr: GHPullRequest) -> dict:
    """Fetch PR-specific data: reviews, CI checks, and diff stats.

    Review status is determined by taking the latest review per reviewer
    (later reviews override earlier ones) and classifying as:
    - 'changes_requested' if any reviewer's latest is CHANGES_REQUESTED
    - 'approved' if all unique reviewers approved
    - 'pending' otherwise

    CI checks are taken from the last commit's check runs.

    Args:
        gh_pr: A PyGithub PullRequest object.

    Returns:
        Dict with review_status, review_count, unresolved_review_comments,
        ci_passing, ci_failing, ci_pending, diff_additions, diff_deletions,
        diff_files_changed.

    """
    # Reviews: take latest review per reviewer
    reviews = list(gh_pr.get_reviews())
    latest_per_reviewer: dict[str, str] = {}
    for review in reviews:
        if review.user and review.state not in ("COMMENTED", "DISMISSED"):
            latest_per_reviewer[review.user.login] = review.state

    if any(s == "CHANGES_REQUESTED" for s in latest_per_reviewer.values()):
        review_status = "changes_requested"
    elif latest_per_reviewer and all(
        s == "APPROVED" for s in latest_per_reviewer.values()
    ):
        review_status = "approved"
    else:
        review_status = "pending"

    # Unresolved review comments: position is None when a comment is resolved
    review_comments = list(gh_pr.get_review_comments())
    unresolved = sum(1 for c in review_comments if c.position is not None)

    # CI checks from last commit
    ci_passing: list[str] = []
    ci_failing: list[str] = []
    ci_pending: list[str] = []
    commits_list = list(gh_pr.get_commits())
    if commits_list:
        last_commit = commits_list[-1]
        for check in last_commit.get_check_runs():
            if check.conclusion in ("success", "skipped", "neutral"):
                ci_passing.append(check.name)
            elif check.conclusion in (
                "failure",
                "cancelled",
                "timed_out",
                "action_required",
            ):
                ci_failing.append(check.name)
            else:
                ci_pending.append(check.name)

    return {
        "review_status": review_status,
        "review_count": len(latest_per_reviewer),
        "unresolved_review_comments": unresolved,
        "ci_passing": ci_passing,
        "ci_failing": ci_failing,
        "ci_pending": ci_pending,
        "diff_additions": gh_pr.additions,
        "diff_deletions": gh_pr.deletions,
        "diff_files_changed": gh_pr.changed_files,
    }
