"""GitHub data collector for issues, PRs, releases, and dependencies."""

import logging
import time
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast

import github
import sqlalchemy as sa
import urllib3
from github import Github, GithubException
from github.Issue import Issue as GHIssue
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.collectors import RateLimitError
from craft_dashboard.collectors import github_issue_store as store
from craft_dashboard.collectors import github_release_store as release_store
from craft_dashboard.collectors.github_adapters import (
    GraphQLIssueAdapter,
    GraphQLPullRequestAdapter,
    interleave_open_graphql_items,
)
from craft_dashboard.collectors.github_graphql import (
    _parse_graphql_datetime,
    fetch_issue_states,
    paginated_issues,
    paginated_pull_requests,
    paginated_releases_and_branches,
)
from craft_dashboard.collectors.github_mapping import (
    build_issue_values,
    classify_change_type,
)
from craft_dashboard.collectors.github_mapping import (
    classify_issue as _classify_issue,
)
from craft_dashboard.collectors.github_releases import (
    select_best_tag,
    select_branches_to_track,
)
from craft_dashboard.collectors.github_rest import (
    fetch_closing_references as _fetch_closing_references,
)
from craft_dashboard.collectors.github_rest import (
    fetch_issue_comments as _fetch_issue_comments,
)
from craft_dashboard.collectors.github_rest import (
    fetch_pr_details as _fetch_pr_details,
)
from craft_dashboard.collectors.github_rest import (
    tag_on_main as _tag_on_main,
)
from craft_dashboard.llm.content_hash import compute_content_hash

__all__ = ["GitHubCollector"]

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from github.Repository import Repository as GHRepository

_CollectedItem = GHIssue | GraphQLIssueAdapter | GraphQLPullRequestAdapter

_PROGRESS_LOG_INTERVAL_SECONDS = 30
_REST_RATE_LIMIT_CHECK_INTERVAL = 25
_MAX_REST_LOOKBACK_DAYS = 90


class RateLimitStatus(TypedDict):
    """Snapshot of both REST (core) and GraphQL API rate-limit budgets."""

    core_remaining: int
    core_limit: int
    core_reset: datetime | None
    graphql_remaining: int
    graphql_limit: int
    graphql_reset: datetime | None


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
        return build_issue_values(
            gh_issue,
            project_id=project_id,
            issue_type=issue_type,
            state=state,
            comments=comments,
            extra_metadata=extra_metadata,
            is_maintainer=self.is_maintainer,
            fetched_at=datetime.now(tz=UTC),
        )

    def check_rate_limit(self) -> RateLimitStatus:
        """Check GitHub REST (core) and GraphQL API rate limit status."""
        overview = self.gh.get_rate_limit()
        core = overview.resources.core
        graphql = overview.resources.graphql
        return {
            "core_remaining": int(core.remaining),
            "core_limit": int(core.limit),
            "core_reset": cast(datetime | None, core.reset),
            "graphql_remaining": int(graphql.remaining),
            "graphql_limit": int(graphql.limit),
            "graphql_reset": cast(datetime | None, graphql.reset),
        }

    def wait_for_rate_limit(
        self, resource: Literal["core", "graphql"] = "core", threshold: int = 100
    ) -> None:
        """Sleep until the given resource's rate limit resets, if nearly exhausted.

        Args:
            resource: Which budget to check — "core" for REST calls (e.g.
                repo.compare()), "graphql" for GraphQL queries.
            threshold: Sleep if remaining requests fall at or below this value.

        """
        quota = self.check_rate_limit()
        if resource == "core":
            remaining = quota["core_remaining"]
            limit = quota["core_limit"]
            reset_at = quota["core_reset"]
        else:
            remaining = quota["graphql_remaining"]
            limit = quota["graphql_limit"]
            reset_at = quota["graphql_reset"]

        if remaining >= threshold:
            return

        if reset_at is None:
            raise RateLimitError(resource=resource, remaining=remaining, limit=limit)

        reset_time = (
            reset_at.replace(tzinfo=UTC) if reset_at.tzinfo is None else reset_at
        )
        sleep_seconds = max(0, int((reset_time - datetime.now(UTC)).total_seconds()))
        if sleep_seconds == 0:
            return

        logger.warning(
            "GitHub %s rate limit low (%d/%d remaining); sleeping for %ds until %s",
            resource,
            remaining,
            limit,
            sleep_seconds,
            reset_time.isoformat(),
        )
        time.sleep(sleep_seconds)

    def _open_issue_source(
        self,
        repo_name: str,
        limit: int,
        since: datetime | None,
    ) -> Iterator[GraphQLIssueAdapter | GraphQLPullRequestAdapter]:
        """Return the GraphQL-backed iterator of currently open issues and PRs.

        Args:
            repo_name: Repository name (without org prefix).
            limit: Maximum number of issues to fetch, for logging only.
            since: Only fetch items updated on or after this timestamp.

        Returns:
            An iterator of GraphQL issue and PR adapters.

        """
        # Always fetch all currently-open issues; no schedule gate.
        # The per-issue updated_at skip below handles efficiency.
        # Use GraphQL (not REST) for the open pass: this runs every 10
        # minutes, and REST's N+1 per-item calls (comments, reviews, CI
        # checks) would blow the REST rate limit at that cadence.
        self.wait_for_rate_limit(resource="graphql")
        requester = self.gh.requester
        gh_issues = interleave_open_graphql_items(
            paginated_issues(requester, self.org, repo_name, since=since),
            paginated_pull_requests(requester, self.org, repo_name, since=since),
        )
        logger.info(
            "  %s/%s: collecting open issues (via GraphQL)%s",
            self.org,
            repo_name,
            f", limit: {limit}" if limit else "",
        )
        return gh_issues

    async def _rest_issue_source(
        self,
        repo: "GHRepository",
        repo_name: str,
        project_id: int,
        session: AsyncSession,
        limit: int,
        refresh_age_days: int,
        since: datetime | None,
        state: Literal["closed", "all", "full"],
    ) -> Iterable[GHIssue] | None:
        """Return the REST-backed iterator of issues due for collection.

        Args:
            repo: The PyGithub repository.
            repo_name: Repository name (without org prefix).
            project_id: The database ID of the project.
            session: An async SQLAlchemy session.
            limit: Maximum number of issues to fetch, for logging only.
            refresh_age_days: Issues last fetched more than this many days ago
                are eligible for re-fetching.
            since: Explicit watermark to fetch from, if any.
            state: Which issues to fetch.

        Returns:
            An iterable of PyGithub issues, or None when nothing is due.

        """
        # Count how many existing issues are due for refresh
        cutoff = datetime.now(tz=UTC) - timedelta(days=refresh_age_days)
        stale_where = store.stale_issue_filter(project_id, cutoff)
        due_count = await store.count_stale_issues(session, stale_where)

        # Also check whether this project has any issues at all (fresh-project detection).
        total_count = await store.count_issues(session, project_id)

        # Check whether closed issues exist in the DB for this project.
        closed_count = await store.count_closed_issues(session, project_id)

        rest_state = "all" if state == "full" else state
        is_full_collection = state == "full" or (since is None and closed_count == 0)

        if (
            not is_full_collection
            and since is None
            and due_count == 0
            and total_count > 0
        ):
            logger.info(
                "  %s/%s: no issues due for refresh, skipping", self.org, repo_name
            )
            return None

        since_date = await self._resolve_rest_since(
            session,
            repo_name,
            stale_where,
            since=since,
            state=state,
            is_full_collection=is_full_collection,
        )

        if since_date is not None:
            gh_issues = repo.get_issues(
                state=rest_state,
                sort="updated",
                direction="desc",
                since=since_date,
            )
            logger.info(
                "  %s/%s: starting collection (%d issues due for refresh, fetching updated since %s)%s",
                self.org,
                repo_name,
                due_count,
                since_date.strftime("%Y-%m-%d"),
                f", limit: {limit}" if limit else "",
            )
        else:
            gh_issues = repo.get_issues(
                state=rest_state, sort="updated", direction="desc"
            )
            logger.info(
                "  %s/%s: starting full collection (all history)%s",
                self.org,
                repo_name,
                f", limit: {limit}" if limit else "",
            )
        return gh_issues

    async def _resolve_rest_since(
        self,
        session: AsyncSession,
        repo_name: str,
        stale_where: sa.ColumnElement[bool],
        *,
        since: datetime | None,
        state: Literal["closed", "all", "full"],
        is_full_collection: bool,
    ) -> datetime | None:
        """Determine the REST 'since' watermark for this collection pass.

        Args:
            session: An async SQLAlchemy session.
            repo_name: Repository name (without org prefix).
            stale_where: Filter matching issues due for refresh.
            since: Explicit watermark supplied by the caller, if any.
            state: Which issues to fetch.
            is_full_collection: Whether this pass fetches all history.

        Returns:
            The timestamp to fetch from, or None for unbounded collection.

        """
        if state == "full":
            # Unbounded collection back to repository creation
            return None
        if since is not None:
            since_date = since.replace(tzinfo=UTC) if since.tzinfo is None else since
            logger.info(
                "  %s/%s: using watermark since %s",
                self.org,
                repo_name,
                since_date.isoformat(),
            )
            return since_date
        if is_full_collection:
            return None

        # Use 'since' based on the oldest last_fetched_at of due issues so we
        # never miss a state transition that happened while the system was offline.
        # Cap at 90 days to bound the amount of data fetched on a long outage.
        max_lookback = datetime.now(tz=UTC) - timedelta(days=_MAX_REST_LOOKBACK_DAYS)
        oldest_fetch = await store.oldest_stale_last_fetched(session, stale_where)
        if oldest_fetch is None:
            # Fresh project with no issues yet: fetch all history.
            return None
        oldest_fetch_tz = (
            oldest_fetch.replace(tzinfo=UTC)
            if oldest_fetch.tzinfo is None
            else oldest_fetch
        )
        return max(oldest_fetch_tz - timedelta(days=1), max_lookback)

    def _is_unchanged(
        self,
        gh_issue: _CollectedItem,
        last_fetched: datetime | None,
        repo_name: str,
    ) -> bool:
        """Report whether an issue is unchanged since it was last fetched.

        Uses updated_at comparison rather than a fixed age window so that
        state transitions (e.g. open → closed) that happened after our last
        fetch are never silently ignored.
        """
        if last_fetched is None or gh_issue.updated_at is None:
            return False
        fetched_tz = (
            last_fetched.replace(tzinfo=UTC)
            if last_fetched.tzinfo is None
            else last_fetched
        )
        issue_updated_at = (
            gh_issue.updated_at.replace(tzinfo=UTC)
            if gh_issue.updated_at.tzinfo is None
            else gh_issue.updated_at
        )
        if issue_updated_at <= fetched_tz:
            logger.debug(
                "  Skipping %s#%d (unchanged since %s)",
                repo_name,
                gh_issue.number,
                fetched_tz.strftime("%Y-%m-%d"),
            )
            return True
        return False

    def _fetch_item_payload(
        self,
        gh_issue: _CollectedItem,
        repo: "GHRepository | None",
        repo_name: str,
        issue_type: str,
        issue_state: str,
    ) -> tuple[list, dict]:
        """Fetch comments and PR/closing-reference metadata for one item.

        GraphQL items carry this data inline; REST items need extra calls,
        which are allowed to fail without aborting the collection pass.

        Args:
            gh_issue: A PyGithub issue or a GraphQL adapter.
            repo: The PyGithub repository, for REST pull-request lookups.
            repo_name: Repository name (without org prefix).
            issue_type: 'issue' or 'pull_request'.
            issue_state: Normalized state string.

        Returns:
            A tuple of (comments, extra_metadata).

        """
        comments: list = []
        if isinstance(gh_issue, (GraphQLIssueAdapter, GraphQLPullRequestAdapter)):
            comments = gh_issue.fetch_comments()
        else:
            try:
                comments = _fetch_issue_comments(gh_issue)
            except GithubException:
                logger.warning(
                    "Failed to fetch comments for %s#%d",
                    repo_name,
                    gh_issue.number,
                    exc_info=True,
                )

        # For all PRs, fetch reviews, CI status, and diff stats.
        # For closed issues, fetch closing references (PRs that closed them).
        extra_metadata: dict = {}
        if issue_type == "pull_request":
            logger.debug(
                "  Fetching PR details for %s/%s#%d",
                self.org,
                repo_name,
                gh_issue.number,
            )
            if isinstance(gh_issue, GraphQLPullRequestAdapter):
                extra_metadata = gh_issue.fetch_pr_details()
            else:
                rest_repo = cast("GHRepository", repo)
                try:
                    gh_pr = rest_repo.get_pull(gh_issue.number)
                    extra_metadata = _fetch_pr_details(gh_pr)
                except GithubException:
                    logger.warning(
                        "Failed to fetch PR details for %s#%d",
                        repo_name,
                        gh_issue.number,
                        exc_info=True,
                    )
        elif issue_state == "closed":
            logger.debug(
                "  Fetching closing references for %s/%s#%d",
                self.org,
                repo_name,
                gh_issue.number,
            )
            if isinstance(gh_issue, GraphQLIssueAdapter):
                closing_refs = gh_issue.fetch_closing_references()
            elif isinstance(gh_issue, GraphQLPullRequestAdapter):
                closing_refs = []
            else:
                closing_refs = _fetch_closing_references(gh_issue)
            if closing_refs:
                extra_metadata["closing_references"] = closing_refs

        return comments, extra_metadata

    async def _reconcile_dropped_open_items(
        self,
        repo_name: str,
        project_id: int,
        session: AsyncSession,
        seen_open_external_ids: set[str],
        collection_run_id: int | None,
    ) -> int:
        """Close out issues that are stored as open but are gone from GitHub's open set.

        Args:
            repo_name: Repository name (without org prefix).
            project_id: The database ID of the project.
            session: An async SQLAlchemy session.
            seen_open_external_ids: External IDs observed in this pass.
            collection_run_id: ID of the current collection run.

        Returns:
            The number of issues reconciled to a closed or merged state.

        """
        db_open_ids = await store.fetch_open_external_ids(session, project_id)
        missing_ids = db_open_ids - seen_open_external_ids
        if not missing_ids:
            return 0

        missing_numbers = [int(eid) for eid in missing_ids if eid.isdigit()]
        logger.info(
            "  %s/%s: reconciling %d dropped open items: %s",
            self.org,
            repo_name,
            len(missing_numbers),
            missing_numbers,
        )
        reconciled = fetch_issue_states(
            self.gh.requester, self.org, repo_name, missing_numbers
        )
        now_utc = datetime.now(UTC)
        count = 0
        for num, status in reconciled.items():
            if status["state"] != "closed":
                continue
            is_merged = bool(status.get("merged_at"))
            closed_at = (
                status["merged_at"] if is_merged else status["closed_at"] or now_utc
            )
            new_state = "merged" if is_merged else "closed"
            new_change_type = "merged" if is_merged else "closed"
            # Look up current issue fields to recompute content_hash and title for activity
            issue_row = await store.fetch_issue_content(session, project_id, str(num))
            curr_title = issue_row[0] if issue_row and issue_row[0] else ""
            new_content_hash = (
                compute_content_hash(
                    curr_title,
                    issue_row[1] if issue_row else None,
                    new_state,
                    issue_row[2] or [] if issue_row else [],
                    comments=issue_row[3] or [] if issue_row else [],
                    pr_details=issue_row[4] if issue_row else None,
                )
                if issue_row
                else None
            )
            await store.mark_issue_closed(
                session,
                project_id=project_id,
                external_id=str(num),
                state=new_state,
                closed_at=closed_at,
                content_hash=new_content_hash,
                last_fetched_at=now_utc,
                collection_run_id=collection_run_id,
            )
            # Record activity entry for issue closure/merge
            store.stage_issue_activity(
                session,
                project_id=project_id,
                issue_number=num,
                change_type=new_change_type,
                title=curr_title[:200],
                occurred_at=closed_at,
                collection_run_id=collection_run_id,
            )
            count += 1
        return count

    async def collect_issues(
        self,
        repo_name: str,
        project_id: int,
        session: AsyncSession,
        limit: int = 0,
        refresh_age_days: int = 7,
        since: datetime | None = None,
        collection_run_id: int | None = None,
        state: Literal["open", "closed", "all", "full"] = "open",
    ) -> int:
        """Collect issues and PRs for a repository.

        Fetches issues from GitHub and upserts them into the database. All
        staged rows are committed once, at the end of a fully successful pass.

        Args:
            repo_name: Repository name (without org prefix).
            project_id: The database ID of the project.
            session: An async SQLAlchemy session.
            limit: Maximum number of issues to fetch per repo (0 = all).
            refresh_age_days: Issues last fetched more than this many days ago
                are considered stale and eligible for re-fetching. Only used
                when state is not "open".
            since: Fetch only issues updated on or after this timestamp. For
                state="open", this filters the GraphQL fetch to recently
                updated issues/PRs to reduce query cost during frequent open
                polling. For non-open states, it continues to drive the
                existing incremental REST refresh behavior.
            collection_run_id: ID of the collection run that fetched these issues.
            state: Which issues to fetch — "open" (always refreshed, no schedule
                gate), "closed" (closed issues only), "all" (open + closed), or
                "full" (unconstrained full refresh of all history). Default is
                "open".

        Returns:
            The number of issues upserted.

        """
        repo: GHRepository | None = None
        gh_issues: Iterable[_CollectedItem]
        seen_open_external_ids: set[str] = set()
        if state == "open":
            gh_issues = self._open_issue_source(repo_name, limit, since)
        else:
            repo = self.gh.get_repo(f"{self.org}/{repo_name}")
            rest_issues = await self._rest_issue_source(
                repo,
                repo_name,
                project_id,
                session,
                limit,
                refresh_age_days,
                since,
                state,
            )
            if rest_issues is None:
                return 0
            gh_issues = rest_issues

        count = 0
        skipped = 0
        last_progress = time.monotonic()

        for gh_issue in gh_issues:
            seen_open_external_ids.add(str(gh_issue.number))
            if limit > 0 and count >= limit:
                logger.info(
                    "Reached issue limit (%d) for %s/%s", limit, self.org, repo_name
                )
                break

            issue_type, issue_state = _classify_issue(cast(GHIssue, gh_issue))

            last_fetched, previous_closed_at = await store.fetch_issue_freshness(
                session, project_id, str(gh_issue.number)
            )
            if self._is_unchanged(gh_issue, last_fetched, repo_name):
                skipped += 1
                continue

            logger.debug(
                "  %s/%s#%d  %s (%s)",
                self.org,
                repo_name,
                gh_issue.number,
                issue_type,
                issue_state,
            )

            comments, extra_metadata = self._fetch_item_payload(
                gh_issue, repo, repo_name, issue_type, issue_state
            )

            store.stage_issue_activity(
                session,
                project_id=project_id,
                issue_number=gh_issue.number,
                change_type=classify_change_type(
                    last_fetched, issue_state, previous_closed_at
                ),
                title=(gh_issue.title or "")[:200],
                occurred_at=gh_issue.updated_at or datetime.now(UTC),
                collection_run_id=collection_run_id,
            )
            await store.upsert_issue(
                session,
                self._build_issue_values(
                    cast(GHIssue, gh_issue),
                    project_id,
                    issue_type,
                    issue_state,
                    comments,
                    extra_metadata,
                ),
                collection_run_id,
            )
            count += 1
            if state != "open" and count % _REST_RATE_LIMIT_CHECK_INTERVAL == 0:
                self.wait_for_rate_limit(resource="core")

            now = time.monotonic()
            if now - last_progress >= _PROGRESS_LOG_INTERVAL_SECONDS:
                logger.info("  %s/%s: %d issues fetched...", self.org, repo_name, count)
                last_progress = now

        # In open mode, reconcile any issues/PRs that were open in our DB
        # but are no longer in GitHub's open set (meaning they were closed/merged).
        if state == "open" and not limit:
            count += await self._reconcile_dropped_open_items(
                repo_name,
                project_id,
                session,
                seen_open_external_ids,
                collection_run_id,
            )

        await session.commit()
        logger.info(
            "Collected %d issues from %s/%s (%d skipped, unchanged since last fetch)",
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
    ) -> int:
        """Collect releases for a repository, one row per branch.

        Enumerates ALL hotfix/* branches from GitHub (no version filter),
        finds the latest matching release tag per branch, and computes
        commits_since_tag and tag_on_main.

        Args:
            repo_name: Repository name (without org prefix).
            project_id: The database ID of the project.
            session: An async SQLAlchemy session.

        Returns:
            Number of branch+release rows upserted.

        """
        self.wait_for_rate_limit(resource="graphql")
        requester = self.gh.requester
        # known_since is always None (no incremental fetch) even though every
        # 10-minute run refetches full release/branch history: the
        # "best tag per branch" selection below needs the complete tag list to
        # correctly pick the highest-version tag per branch, not just tags
        # newer than the last watermark. Passing a real known_since here would
        # silently break that selection for branches with no new releases
        # since the watermark. The paginated_releases_and_branches() early-stop
        # optimization exists and is unit-tested but is intentionally unused
        # in production for this reason.
        release_nodes, branch_names = paginated_releases_and_branches(
            requester, self.org, repo_name, known_since=None
        )
        repo = self.gh.get_repo(f"{self.org}/{repo_name}")

        # Collect all non-prerelease, non-draft releases
        all_releases: dict[str, dict[str, Any]] = {}
        for node in release_nodes:
            if not node["isPrerelease"] and not node["isDraft"]:
                all_releases[node["tagName"]] = node

        logger.info(
            "  %s/%s: found %d non-prerelease tags",
            self.org,
            repo_name,
            len(all_releases),
        )

        branches_to_track = select_branches_to_track(branch_names)

        logger.info(
            "  %s/%s: tracking branches: %s", self.org, repo_name, branches_to_track
        )

        count = 0
        for branch_name in branches_to_track:
            best_tag = select_best_tag(branch_name, list(all_releases))

            if not best_tag:
                logger.debug(
                    "  %s/%s: no matching tag for branch %s",
                    self.org,
                    repo_name,
                    branch_name,
                )
                continue

            node = all_releases[best_tag]
            pub = _parse_graphql_datetime(node["publishedAt"] or node["createdAt"])
            metadata: dict = {"prerelease": False, "draft": False}

            await release_store.upsert_release(
                session,
                project_id=project_id,
                version=best_tag,
                branch=branch_name,
                released_at=pub if pub else None,
                is_hotfix=(branch_name != "main"),
                metadata=metadata,
            )
            count += 1

            # Compute commits since tag
            try:
                comparison = repo.compare(best_tag, branch_name)
                commits_since = comparison.ahead_by
                meta = await release_store.fetch_release_metadata(
                    session, project_id, branch_name
                )
                meta["commits_since_tag"] = commits_since
                if branch_name != "main":
                    meta["tag_on_main"] = _tag_on_main(repo, best_tag)
                await release_store.update_release_metadata(
                    session, project_id, branch_name, meta
                )
                logger.info(
                    "  %s@%s: %d commits since %s",
                    repo_name,
                    branch_name,
                    commits_since,
                    best_tag,
                )
            except Exception:  # noqa: BLE001 - missing compare data should not abort release collection
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
