"""GitHub data collector for issues, PRs, releases, and dependencies."""

import hashlib
import logging
from datetime import UTC, datetime

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
    ) -> int:
        """Collect all open issues and PRs for a repository.

        Fetches issues from GitHub and upserts them into the database.

        Args:
            repo_name: Repository name (without org prefix).
            project_id: The database ID of the project.
            session: An async SQLAlchemy session.

        Returns:
            The number of issues upserted.

        """
        from sqlalchemy.dialects.postgresql import insert  # noqa: PLC0415

        from craft_dashboard.models.issue import Issue  # noqa: PLC0415

        repo = self.gh.get_repo(f"{self.org}/{repo_name}")
        gh_issues = repo.get_issues(state="all")
        count = 0

        for gh_issue in gh_issues:
            issue_type, state = _classify_issue(gh_issue)
            label_names = [label.name for label in gh_issue.labels]
            author = gh_issue.user.login if gh_issue.user else None

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
