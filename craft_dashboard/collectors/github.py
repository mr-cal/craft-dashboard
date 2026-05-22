"""GitHub data collector for issues, PRs, releases, and dependencies."""

import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta

import github
import sqlalchemy as sa
import urllib3
from github import Github, GithubException
from github.Issue import Issue as GHIssue
from github.PullRequest import PullRequest as GHPullRequest
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.collectors import ISSUE_UPSERT_FIELDS

__all__ = ["GitHubCollector"]

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


def _fetch_issue_comments(gh_issue: GHIssue) -> list[dict]:
    """Fetch the last 10 comments from a GitHub issue.

    Args:
        gh_issue: A PyGithub Issue object.

    Returns:
        List of comment dicts, each with author/body/created_at/type.

    """
    comments = list(gh_issue.get_comments())
    # Keep only the last 10
    recent = comments[-10:]
    return [
        {
            "author": c.user.login if c.user else "unknown",
            "body": (c.body or "")[:1000],
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "type": "comment",
        }
        for c in recent
    ]


def _fetch_pr_details(gh_pr: GHPullRequest) -> dict:
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
        retry = urllib3.Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        self.gh = Github(
            auth=github.Auth.Token(token),
            timeout=30,
            retry=retry,
        )
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

    def _build_issue_values(
        self,
        gh_issue: GHIssue,
        project_id: int,
        issue_type: str,
        state: str,
        comments: list,
        extra_metadata: dict,
    ) -> dict:
        """Build the values dict for an issue upsert.

        Args:
            gh_issue: A PyGithub Issue object.
            project_id: The database ID of the project.
            issue_type: 'issue' or 'pull_request'.
            state: Normalized state string.
            comments: Fetched comment dicts.
            extra_metadata: PR details or other metadata.

        Returns:
            Dict of column values for the insert statement.

        """
        author = gh_issue.user.login if gh_issue.user else None
        return {
            "project_id": project_id,
            "source": "github",
            "external_id": str(gh_issue.number),
            "issue_type": issue_type,
            "title": gh_issue.title,
            "body": gh_issue.body,
            "state": state,
            "author": author,
            "author_is_maintainer": self.is_maintainer(author) if author else False,
            "author_is_bot": author.endswith("[bot]") if author else False,
            "labels": [label.name for label in gh_issue.labels],
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
            "last_fetched_at": datetime.now(tz=UTC),
        }

    async def collect_issues(
        self,
        repo_name: str,
        project_id: int,
        session: AsyncSession,
        limit: int = 0,
        refresh_age_days: int = 7,
    ) -> int:
        """Collect issues and PRs for a repository.

        Fetches issues from GitHub and upserts them into the database.

        Args:
            repo_name: Repository name (without org prefix).
            project_id: The database ID of the project.
            session: An async SQLAlchemy session.
            limit: Maximum number of issues to fetch per repo (0 = all).
            refresh_age_days: Skip issues fetched within this many days.

        Returns:
            The number of issues upserted.

        """
        from sqlalchemy.dialects.postgresql import (
            insert,
        )

        from craft_dashboard.models.issue import (
            Issue,
        )

        # Count how many existing issues are due for refresh
        cutoff = datetime.now(tz=UTC) - timedelta(days=refresh_age_days)
        due_count_result = await session.execute(
            sa.select(sa.func.count())
            .select_from(Issue)
            .where(
                Issue.project_id == project_id,
                Issue.source == "github",
                sa.or_(
                    Issue.last_fetched_at == None,  # noqa: E711 — SQLAlchemy requires == for IS NULL SQL generation
                    Issue.last_fetched_at < cutoff,
                ),
            )
        )
        due_count = due_count_result.scalar_one()

        if due_count == 0:
            logger.info(
                "  %s/%s: no issues due for refresh, skipping", self.org, repo_name
            )
            return 0

        repo = self.gh.get_repo(f"{self.org}/{repo_name}")

        # Use 'since' parameter to avoid paginating through all old issues.
        # Only fetch issues updated after the refresh cutoff, plus a 1-day buffer.
        since_date = cutoff - timedelta(days=1)
        gh_issues = repo.get_issues(
            state="all", sort="updated", direction="desc", since=since_date
        )

        logger.info(
            "  %s/%s: starting collection (%d issues due for refresh, fetching updated since %s)%s",
            self.org,
            repo_name,
            due_count,
            since_date.strftime("%Y-%m-%d"),
            f", limit: {limit}" if limit else "",
        )

        count = 0
        skipped = 0
        last_progress = time.monotonic()

        for gh_issue in gh_issues:
            if limit > 0 and count >= limit:
                logger.info(
                    "Reached issue limit (%d) for %s/%s", limit, self.org, repo_name
                )
                break

            issue_type, state = _classify_issue(gh_issue)

            # Check if this issue was recently fetched
            existing = await session.execute(
                sa.select(Issue.last_fetched_at).where(
                    Issue.project_id == project_id,
                    Issue.source == "github",
                    Issue.external_id == str(gh_issue.number),
                )
            )
            last_fetched = existing.scalar_one_or_none()
            if last_fetched is not None:
                fetched_tz = (
                    last_fetched.replace(tzinfo=UTC)
                    if last_fetched.tzinfo is None
                    else last_fetched
                )
                age = (datetime.now(tz=UTC) - fetched_tz).days
                if age < refresh_age_days:
                    logger.debug(
                        "  Skipping %s#%d (fetched %d days ago)",
                        repo_name,
                        gh_issue.number,
                        age,
                    )
                    skipped += 1
                    continue

            logger.debug(
                "  %s/%s#%d  %s (%s)",
                self.org,
                repo_name,
                gh_issue.number,
                issue_type,
                state,
            )

            # Fetch comments for all items (open and closed)
            comments: list = []
            try:
                comments = _fetch_issue_comments(gh_issue)
            except GithubException:
                logger.warning(
                    "Failed to fetch comments for %s#%d",
                    repo_name,
                    gh_issue.number,
                    exc_info=True,
                )

            # For all PRs, fetch reviews, CI status, and diff stats
            extra_metadata: dict = {}
            if issue_type == "pull_request":
                logger.debug(
                    "  Fetching PR details for %s/%s#%d",
                    self.org,
                    repo_name,
                    gh_issue.number,
                )
                try:
                    gh_pr = repo.get_pull(gh_issue.number)
                    extra_metadata = _fetch_pr_details(gh_pr)
                except GithubException:
                    logger.warning(
                        "Failed to fetch PR details for %s#%d",
                        repo_name,
                        gh_issue.number,
                        exc_info=True,
                    )

            stmt = insert(Issue).values(
                **self._build_issue_values(
                    gh_issue,
                    project_id,
                    issue_type,
                    state,
                    comments,
                    extra_metadata,
                )
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["project_id", "source", "external_id"],
                set_={
                    field: getattr(stmt.excluded, field)
                    for field in ISSUE_UPSERT_FIELDS
                }
                | {
                    "metadata": stmt.excluded.metadata,
                    "comments": stmt.excluded.comments,
                },
            )
            await session.execute(stmt)
            count += 1

            now = time.monotonic()
            if now - last_progress >= 30:
                logger.info("  %s/%s: %d issues fetched...", self.org, repo_name, count)
                last_progress = now

        await session.commit()
        logger.info(
            "Collected %d issues from %s/%s (%d skipped, recently fetched)",
            count,
            self.org,
            repo_name,
            skipped,
        )
        return count

    async def collect_releases(
        self,
        repo_name: str,
        project_id: int,
        session: AsyncSession,
        hotfix_min_version: str | None = None,
    ) -> int:
        """Collect releases for a repository, one row per branch.

        Enumerates hotfix/* branches from GitHub, finds the latest matching
        release tag per branch, and computes commits_since_tag.

        Args:
            repo_name: Repository name (without org prefix).
            project_id: The database ID of the project.
            session: An async SQLAlchemy session.
            hotfix_min_version: Minimum version string (e.g. "3.0") for hotfix branches.

        Returns:
            Number of branch+release rows upserted.

        """
        import re  # noqa: PLC0415 — deferred import

        from sqlalchemy.dialects.postgresql import (
            insert,
        )

        from craft_dashboard.models.release import (
            Release,
        )

        hotfix_re = re.compile(r"^hotfix/(\d+)\.(\d+)$")

        repo = self.gh.get_repo(f"{self.org}/{repo_name}")

        # Collect all non-prerelease, non-draft releases
        all_releases: dict[str, object] = {}  # tag_name → GH release object
        for gh_rel in repo.get_releases():
            if not gh_rel.prerelease and not gh_rel.draft:
                all_releases[gh_rel.tag_name] = gh_rel

        logger.info(
            "  %s/%s: found %d non-prerelease tags",
            self.org,
            repo_name,
            len(all_releases),
        )

        # Determine branches to track
        branches_to_track: list[str] = ["main"]

        # Parse min version filter
        min_major, min_minor = 0, 0
        if hotfix_min_version:
            try:
                parts = hotfix_min_version.split(".")
                min_major, min_minor = int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                logger.warning(
                    "Could not parse hotfix_min_version %r", hotfix_min_version
                )

        # List hotfix/* branches from GitHub
        for branch in repo.get_branches():
            m = hotfix_re.match(branch.name)
            if m:
                major, minor = int(m.group(1)), int(m.group(2))
                if (major, minor) >= (min_major, min_minor):
                    branches_to_track.append(branch.name)

        logger.info(
            "  %s/%s: tracking branches: %s", self.org, repo_name, branches_to_track
        )

        def parse_version(tag: str) -> tuple[int, ...] | None:
            """Parse a version tag like '4.2.1' or 'v4.2.1' into a tuple."""
            clean = tag.lstrip("v")
            try:
                return tuple(int(p) for p in clean.split("."))
            except ValueError:
                return None

        count = 0
        for branch_name in branches_to_track:
            # Find the best matching tag for this branch
            best_tag: str | None = None
            best_ver: tuple[int, ...] = ()

            if branch_name == "main":
                # Latest tag overall
                for tag in all_releases:
                    ver = parse_version(tag)
                    if ver and ver > best_ver:
                        best_ver = ver
                        best_tag = tag
            else:
                # hotfix/X.Y → latest X.Y.* tag
                m = hotfix_re.match(branch_name)
                if not m:
                    continue
                hf_major, hf_minor = int(m.group(1)), int(m.group(2))
                for tag in all_releases:
                    ver = parse_version(tag)
                    if (
                        ver
                        and len(ver) >= 2
                        and ver[0] == hf_major
                        and ver[1] == hf_minor
                    ) and ver > best_ver:
                        best_ver = ver
                        best_tag = tag

            if not best_tag:
                logger.debug(
                    "  %s/%s: no matching tag for branch %s",
                    self.org,
                    repo_name,
                    branch_name,
                )
                continue

            gh_rel = all_releases[best_tag]
            pub = gh_rel.published_at
            metadata: dict = {"prerelease": False, "draft": False}

            # Upsert: one row per project+branch
            stmt = insert(Release).values(
                project_id=project_id,
                version=best_tag,
                branch=branch_name,
                released_at=pub.replace(tzinfo=UTC) if pub else None,
                is_hotfix=(branch_name != "main"),
                metadata_=metadata,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["project_id", "branch"],
                set_={
                    "version": stmt.excluded.version,
                    "released_at": stmt.excluded.released_at,
                    "is_hotfix": stmt.excluded.is_hotfix,
                    "metadata": stmt.excluded.metadata,
                },
            )
            await session.execute(stmt)
            count += 1

            # Compute commits since tag
            try:
                comparison = repo.compare(best_tag, branch_name)
                commits_since = comparison.ahead_by
                result = await session.execute(
                    sa.select(Release.metadata_).where(
                        Release.project_id == project_id,
                        Release.branch == branch_name,
                    )
                )
                meta = result.scalar_one_or_none() or {}
                meta["commits_since_tag"] = commits_since
                await session.execute(
                    sa.update(Release)
                    .where(
                        Release.project_id == project_id, Release.branch == branch_name
                    )
                    .values(metadata_=meta)
                )
                logger.info(
                    "  %s@%s: %d commits since %s",
                    repo_name,
                    branch_name,
                    commits_since,
                    best_tag,
                )
            except Exception:  # noqa: BLE001 — individual release errors should not abort collection
                logger.warning(
                    "  Could not compute commits for %s@%s",
                    repo_name,
                    branch_name,
                    exc_info=True,
                )

        await session.commit()
        logger.info(
            "Collected releases for %s/%s (%d branches)", self.org, repo_name, count
        )
        return count
