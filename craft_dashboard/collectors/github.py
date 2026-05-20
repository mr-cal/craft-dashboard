"""GitHub data collector for issues, PRs, releases, and dependencies."""

import hashlib
import logging
from datetime import UTC, datetime

import sqlalchemy as sa
from github import Github
from github.Issue import Issue as GHIssue

logger = logging.getLogger(__name__)


def _classify_issue(gh_issue: GHIssue) -> tuple[str, str]:
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


def _compute_issue_hash(
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


def _fetch_issue_comments(gh_issue) -> list[dict]:  # noqa: ANN001
    """Fetch the last 10 comments from a GitHub issue.

    Args:
        gh_issue: A PyGithub Issue object.

    Returns:
        List of comment dicts, each with author/body/created_at/type.

    """
    comments = list(gh_issue.get_comments())
    # Keep only the last 10
    recent = comments[-10:]
    result = []
    for c in recent:
        result.append({
            "author": c.user.login if c.user else "unknown",
            "body": (c.body or "")[:1000],
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "type": "comment",
        })
    return result


def _fetch_pr_details(gh_pr) -> dict:  # noqa: ANN001
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
            elif check.conclusion in ("failure", "cancelled", "timed_out", "action_required"):
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


class GitHubCollector:
    """Collects issue, PR, release, and dependency data from GitHub."""

    def __init__(
        self,
        token: str,
        org: str = "canonical",
        maintainers: list[str] | None = None,
    ) -> None:
        """Initialize the GitHub collector.

        Args:
            token: GitHub personal access token.
            org: GitHub organization name.
            maintainers: List of GitHub usernames considered maintainers.

        """
        self.gh = Github(token)
        self.org = org
        self.maintainers = set(maintainers or [])

    def is_maintainer(self, username: str) -> bool:
        """Check if a username belongs to a project maintainer.

        Args:
            username: GitHub username to check.

        Returns:
            True if the user is a maintainer.

        """
        return username in self.maintainers

    async def collect_issues(
        self,
        repo_name: str,
        project_id: int,
        session,  # noqa: ANN001
        limit: int = 0,
    ) -> int:
        """Collect issues and PRs for a repository.

        Fetches issues from GitHub and upserts them into the database.

        Args:
            repo_name: Repository name (without org prefix).
            project_id: The database ID of the project.
            session: An async SQLAlchemy session.
            limit: Maximum number of issues to fetch per repo (0 = all).

        Returns:
            The number of issues upserted.

        """
        from sqlalchemy.dialects.postgresql import insert  # noqa: PLC0415

        from craft_dashboard.models.issue import Issue  # noqa: PLC0415

        repo = self.gh.get_repo(f"{self.org}/{repo_name}")
        gh_issues = repo.get_issues(state="all")
        count = 0

        for gh_issue in gh_issues:
            if limit > 0 and count >= limit:
                logger.info("Reached issue limit (%d) for %s/%s", limit, self.org, repo_name)
                break
            issue_type, state = _classify_issue(gh_issue)
            label_names = [label.name for label in gh_issue.labels]
            author = gh_issue.user.login if gh_issue.user else None

            # Fetch comments for all items (open and closed)
            comments: list = []
            try:
                comments = _fetch_issue_comments(gh_issue)
            except Exception:
                logger.warning(
                    "Failed to fetch comments for %s#%d",
                    repo_name,
                    gh_issue.number,
                    exc_info=True,
                )

            # For all PRs, fetch reviews, CI status, and diff stats
            extra_metadata: dict = {}
            if issue_type == "pull_request":
                try:
                    gh_pr = repo.get_pull(gh_issue.number)
                    extra_metadata = _fetch_pr_details(gh_pr)
                except Exception:
                    logger.warning(
                        "Failed to fetch PR details for %s#%d",
                        repo_name,
                        gh_issue.number,
                        exc_info=True,
                    )

            stmt = insert(Issue).values(
                project_id=project_id,
                source="github",
                external_id=str(gh_issue.number),
                issue_type=issue_type,
                title=gh_issue.title,
                body=gh_issue.body,
                state=state,
                author=author,
                author_is_maintainer=self.is_maintainer(author) if author else False,
                labels=label_names,
                created_at=gh_issue.created_at.replace(tzinfo=UTC)
                if gh_issue.created_at
                else None,
                updated_at=gh_issue.updated_at.replace(tzinfo=UTC)
                if gh_issue.updated_at
                else None,
                closed_at=gh_issue.closed_at.replace(tzinfo=UTC)
                if gh_issue.closed_at
                else None,
                url=gh_issue.html_url,
                metadata_=extra_metadata,
                comments=comments,
                last_fetched_at=datetime.now(tz=UTC),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["project_id", "source", "external_id"],
                set_={
                    "title": stmt.excluded.title,
                    "body": stmt.excluded.body,
                    "state": stmt.excluded.state,
                    "author": stmt.excluded.author,
                    "author_is_maintainer": stmt.excluded.author_is_maintainer,
                    "labels": stmt.excluded.labels,
                    "updated_at": stmt.excluded.updated_at,
                    "closed_at": stmt.excluded.closed_at,
                    "metadata": sa.literal_column("excluded.metadata"),
                    "comments": stmt.excluded.comments,
                    "last_fetched_at": stmt.excluded.last_fetched_at,
                },
            )
            await session.execute(stmt)
            count += 1

        await session.commit()
        logger.info("Collected %d issues from %s/%s", count, self.org, repo_name)
        return count

    async def collect_releases(
        self,
        repo_name: str,
        project_id: int,
        session,  # noqa: ANN001
    ) -> int:
        """Collect releases for a repository.

        Args:
            repo_name: Repository name (without org prefix).
            project_id: The database ID of the project.
            session: An async SQLAlchemy session.

        Returns:
            The number of releases upserted.

        """
        from sqlalchemy.dialects.postgresql import insert  # noqa: PLC0415

        from craft_dashboard.models.release import Release  # noqa: PLC0415

        repo = self.gh.get_repo(f"{self.org}/{repo_name}")
        count = 0

        for gh_release in repo.get_releases():
            stmt = insert(Release).values(
                project_id=project_id,
                version=gh_release.tag_name,
                branch=gh_release.target_commitish,
                released_at=gh_release.published_at.replace(tzinfo=UTC)
                if gh_release.published_at
                else None,
                is_hotfix=False,
                metadata_={
                    "prerelease": gh_release.prerelease,
                    "draft": gh_release.draft,
                },
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["project_id", "version"],
                set_={
                    "branch": stmt.excluded.branch,
                    "released_at": stmt.excluded.released_at,
                },
            )
            await session.execute(stmt)
            count += 1

        await session.commit()
        logger.info("Collected %d releases from %s/%s", count, self.org, repo_name)
        return count
