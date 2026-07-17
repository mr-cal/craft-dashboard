"""GraphQL query/pagination layer for the GitHub collector.

Thin wrapper around ``Github.requester.graphql_query`` (PyGithub's built-in
GraphQL client) — no separate HTTP client is introduced. Each generator
yields normalized dicts and logs the ``rateLimit`` block returned alongside
the data so callers can track the GraphQL budget without an extra request.
"""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from github.Requester import Requester

__all__ = [
    "paginated_issues",
    "paginated_pull_requests",
    "paginated_releases_and_branches",
    "classify_pr_review_status",
    "classify_pr_ci_checks",
    "GraphQLCost",
]

logger = logging.getLogger(__name__)

_ISSUES_QUERY = """
query($owner: String!, $name: String!, $after: String, $since: DateTime) {
  rateLimit { cost remaining resetAt }
  repository(owner: $owner, name: $name) {
    issues(first: 50, after: $after, states: [OPEN], filterBy: {since: $since}, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        body
        state
        createdAt
        updatedAt
        closedAt
        url
        author { login }
        labels(first: 20) { nodes { name } }
        comments(last: 10) { nodes { author { login } body createdAt } }
        timelineItems(last: 20, itemTypes: [CROSS_REFERENCED_EVENT]) {
          nodes {
            ... on CrossReferencedEvent {
              source {
                ... on PullRequest { number title url state mergedAt }
              }
            }
          }
        }
      }
    }
  }
}
"""

_PULL_REQUESTS_QUERY = """
query($owner: String!, $name: String!, $after: String) {
  rateLimit { cost remaining resetAt }
  repository(owner: $owner, name: $name) {
    pullRequests(first: 50, after: $after, states: [OPEN], orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        body
        state
        createdAt
        updatedAt
        closedAt
        mergedAt
        url
        author { login }
        labels(first: 20) { nodes { name } }
        additions
        deletions
        changedFiles
        comments(last: 10) { nodes { author { login } body createdAt } }
        reviews(last: 20) { nodes { author { login } state } }
        reviewThreads(first: 50) { nodes { isResolved } }
        commits(last: 1) {
          nodes {
            commit {
              checkSuites(first: 10) {
                nodes { checkRuns(first: 20) { nodes { name conclusion status } } }
              }
            }
          }
        }
      }
    }
  }
}
"""

_RELEASES_AND_BRANCHES_QUERY = """
query($owner: String!, $name: String!, $after: String) {
  rateLimit { cost remaining resetAt }
  repository(owner: $owner, name: $name) {
    releases(first: 20, after: $after, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { tagName isPrerelease isDraft createdAt publishedAt }
    }
    refs(refPrefix: "refs/heads/", query: "hotfix/", first: 100) {
      nodes { name }
    }
  }
}
"""


def _parse_graphql_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class GraphQLCost:
    """Parsed ``rateLimit`` block from a GraphQL response, for budget logging."""

    def __init__(self, cost: int, remaining: int, reset_at: datetime | None) -> None:
        self.cost = cost
        self.remaining = remaining
        self.reset_at = reset_at

    @classmethod
    def from_response(cls, rate_limit: dict[str, Any]) -> "GraphQLCost":
        """Build a cost snapshot from a GraphQL ``rateLimit`` response block."""
        return cls(
            cost=int(rate_limit["cost"]),
            remaining=int(rate_limit["remaining"]),
            reset_at=_parse_graphql_datetime(rate_limit.get("resetAt")),
        )


def paginated_issues(
    requester: "Requester",
    owner: str,
    name: str,
    since: datetime | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield normalized open-issue GraphQL nodes for one repo, following pagination.

    Args:
        requester: ``Github.requester`` — the same authenticated client used
            by every REST call elsewhere in the collector.
        owner: Repository owner/org (e.g. ``"canonical"``).
        name: Repository name (without owner prefix).
        since: Only fetch issues updated on or after this timestamp
            (GraphQL ``filterBy: {since: ...}``). ``None`` fetches all open
            issues.

    Yields:
        Raw GraphQL issue node dicts (one per open issue), in
        updated-at-descending order.

    """
    after: str | None = None
    since_str = (
        since.astimezone(UTC).isoformat().replace("+00:00", "Z") if since else None
    )
    while True:
        _, response = requester.graphql_query(
            _ISSUES_QUERY,
            {"owner": owner, "name": name, "after": after, "since": since_str},
        )
        data = response["data"]
        cost = GraphQLCost.from_response(data["rateLimit"])
        logger.debug(
            "GraphQL issues page for %s/%s: cost=%d remaining=%d reset_at=%s",
            owner,
            name,
            cost.cost,
            cost.remaining,
            cost.reset_at,
        )
        issues = data["repository"]["issues"]
        yield from issues["nodes"]

        page_info = issues["pageInfo"]
        if not page_info["hasNextPage"]:
            return
        after = page_info["endCursor"]


def paginated_pull_requests(
    requester: "Requester",
    owner: str,
    name: str,
    since: datetime | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield normalized open-PR GraphQL nodes for one repo, following pagination.

    ``pullRequests`` has no server-side ``since``/``filterBy`` argument (unlike
    ``issues``), so filtering by ``since`` is done client-side against each
    node's ``updatedAt``.

    Args:
        requester: ``Github.requester``.
        owner: Repository owner/org.
        name: Repository name.
        since: Only yield PRs whose ``updatedAt`` is on or after this
            timestamp. ``None`` yields all open PRs.

    Yields:
        Raw GraphQL PR node dicts, in updated-at-descending order.

    """
    after: str | None = None
    while True:
        _, response = requester.graphql_query(
            _PULL_REQUESTS_QUERY, {"owner": owner, "name": name, "after": after}
        )
        data = response["data"]
        cost = GraphQLCost.from_response(data["rateLimit"])
        logger.debug(
            "GraphQL PRs page for %s/%s: cost=%d remaining=%d reset_at=%s",
            owner,
            name,
            cost.cost,
            cost.remaining,
            cost.reset_at,
        )
        prs = data["repository"]["pullRequests"]
        for node in prs["nodes"]:
            updated_at = _parse_graphql_datetime(node["updatedAt"])
            if since is not None and updated_at is not None and updated_at < since:
                continue
            yield node

        page_info = prs["pageInfo"]
        if not page_info["hasNextPage"]:
            return
        after = page_info["endCursor"]


def paginated_releases_and_branches(
    requester: "Requester",
    owner: str,
    name: str,
    known_since: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch releases (newest-first) and hotfix branch names for one repo.

    Pagination stops early once a release's ``createdAt`` falls at or before
    ``known_since`` (the latest ``released_at`` already stored for this repo),
    since releases are fetched newest-first and everything after that point
    is already known. The ``refs`` (hotfix branches) query only runs on the
    first page — branch listings don't need "since" filtering since
    ``refs(first: 100)`` is cheap and branches are typically few.

    Args:
        requester: ``Github.requester``.
        owner: Repository owner/org.
        name: Repository name.
        known_since: The latest ``Release.released_at`` already stored for
            this repo, or ``None`` to fetch the full release history (e.g.
            on first collection for a repo).

    Returns:
        A tuple of (release node dicts newer than ``known_since``, hotfix
        branch name list).

    """
    after: str | None = None
    releases: list[dict[str, Any]] = []
    branch_names: list[str] = []
    first_page = True

    while True:
        _, response = requester.graphql_query(
            _RELEASES_AND_BRANCHES_QUERY,
            {"owner": owner, "name": name, "after": after},
        )
        data = response["data"]
        cost = GraphQLCost.from_response(data["rateLimit"])
        logger.debug(
            "GraphQL releases page for %s/%s: cost=%d remaining=%d reset_at=%s",
            owner,
            name,
            cost.cost,
            cost.remaining,
            cost.reset_at,
        )
        repo = data["repository"]
        if first_page:
            branch_names = [ref["name"] for ref in repo["refs"]["nodes"]]
            first_page = False

        page = repo["releases"]
        reached_known = False
        for node in page["nodes"]:
            created_at = _parse_graphql_datetime(node["createdAt"])
            if (
                known_since is not None
                and created_at is not None
                and created_at <= known_since
            ):
                reached_known = True
                break
            releases.append(node)

        if reached_known or not page["pageInfo"]["hasNextPage"]:
            return releases, branch_names
        after = page["pageInfo"]["endCursor"]


def classify_pr_review_status(reviews: list[dict[str, Any]]) -> tuple[str, int]:
    """Classify PR review status from GraphQL review nodes.

    Later reviews override earlier ones for the same reviewer;
    ``COMMENTED``/``DISMISSED`` don't count.

    Args:
        reviews: List of ``{"author": {"login": str} | None, "state": str}``
            dicts, in chronological order (GraphQL's default).

    Returns:
        A tuple of (review_status, distinct_reviewer_count).

    """
    latest_per_reviewer: dict[str, str] = {}
    for review in reviews:
        author = review.get("author")
        state = review["state"]
        if author and state not in ("COMMENTED", "DISMISSED"):
            latest_per_reviewer[author["login"]] = state

    if any(s == "CHANGES_REQUESTED" for s in latest_per_reviewer.values()):
        review_status = "changes_requested"
    elif latest_per_reviewer and all(
        s == "APPROVED" for s in latest_per_reviewer.values()
    ):
        review_status = "approved"
    else:
        review_status = "pending"

    return review_status, len(latest_per_reviewer)


def classify_pr_ci_checks(
    commits: list[dict[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    """Classify CI check runs from the last commit's check suites.

    Args:
        commits: The ``commits(last: 1) { nodes { commit { checkSuites ... } } }``
            list from a GraphQL PR node.

    Returns:
        A tuple of (ci_passing, ci_failing, ci_pending) name lists.

    """
    ci_passing: list[str] = []
    ci_failing: list[str] = []
    ci_pending: list[str] = []
    if not commits:
        return ci_passing, ci_failing, ci_pending

    last_commit = commits[-1]["commit"]
    for suite in last_commit["checkSuites"]["nodes"]:
        for check in suite["checkRuns"]["nodes"]:
            conclusion = (check.get("conclusion") or "").upper()
            if conclusion in ("SUCCESS", "SKIPPED", "NEUTRAL"):
                ci_passing.append(check["name"])
            elif conclusion in (
                "FAILURE",
                "CANCELLED",
                "TIMED_OUT",
                "ACTION_REQUIRED",
            ):
                ci_failing.append(check["name"])
            else:
                ci_pending.append(check["name"])

    return ci_passing, ci_failing, ci_pending
