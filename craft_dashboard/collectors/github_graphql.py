"""GraphQL query/pagination layer for the GitHub collector.

Thin wrapper around ``Github.requester.graphql_query`` (PyGithub's built-in
GraphQL client) — no separate HTTP client is introduced. Each generator
yields normalized dicts and logs the ``rateLimit`` block returned alongside
the data so callers can track the GraphQL budget without an extra request.
"""

import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from github import GithubException

if TYPE_CHECKING:
    from github.Requester import Requester

__all__ = [
    "paginated_issues",
    "paginated_pull_requests",
    "paginated_releases_and_branches",
    "fetch_issue_states",
    "classify_pr_review_status",
    "classify_pr_ci_checks",
    "GraphQLCost",
]

logger = logging.getLogger(__name__)

_ISSUES_QUERY = """
query($owner: String!, $name: String!, $after: String, $since: DateTime, $states: [IssueState!] = [OPEN]) {
  rateLimit { cost remaining resetAt }
  repository(owner: $owner, name: $name) {
    issues(first: 50, after: $after, states: $states, filterBy: {since: $since}, orderBy: {field: UPDATED_AT, direction: DESC}) {
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
query($owner: String!, $name: String!, $after: String, $states: [PullRequestState!] = [OPEN]) {
  rateLimit { cost remaining resetAt }
  repository(owner: $owner, name: $name) {
    pullRequests(first: 50, after: $after, states: $states, orderBy: {field: UPDATED_AT, direction: DESC}) {
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
    tags: refs(refPrefix: "refs/tags/", first: 100, orderBy: {field: TAG_COMMIT_DATE, direction: DESC}) {
      nodes {
        name
        target {
          ... on Tag {
            tagger { date }
            target {
              ... on Commit {
                committedDate
              }
            }
          }
          ... on Commit {
            committedDate
          }
        }
      }
    }
  }
}
"""

_SEMVER_TAG_REGEX = re.compile(r"^v?\d+\.\d+\.\d+$")


def _extract_tag_date(target: dict[str, Any] | None) -> str | None:
    """Extract an ISO timestamp string from a git tag GraphQL target."""
    if not target or not isinstance(target, dict):
        return None
    tagger = target.get("tagger")
    if isinstance(tagger, dict) and tagger.get("date"):
        return str(tagger["date"])
    if target.get("committedDate"):
        return str(target["committedDate"])
    nested_target = target.get("target")
    if isinstance(nested_target, dict) and nested_target.get("committedDate"):
        return str(nested_target["committedDate"])
    return None


# Cap any GraphQL error summary we log/raise ourselves. Without this,
# unrecoverable errors would still surface PyGithub's raw exception message,
# which embeds the *entire* response body (see _graphql_query below).
_MAX_ERROR_SUMMARY_LENGTH = 500


def _summarize_graphql_errors(errors: list[Any]) -> str:
    """Build a short, human-readable summary from a GraphQL ``errors`` list."""
    messages = []
    for error in errors:
        if isinstance(error, dict):
            error_type = error.get("type")
            message = error.get("message", "")
            messages.append(f"{error_type}: {message}" if error_type else message)
        else:
            messages.append(str(error))
    summary = "; ".join(dict.fromkeys(m for m in messages if m)) or "unknown error"
    if len(summary) > _MAX_ERROR_SUMMARY_LENGTH:
        summary = summary[:_MAX_ERROR_SUMMARY_LENGTH] + "... (truncated)"
    return summary


def _graphql_query(
    requester: "Requester",
    query: str,
    variables: dict[str, Any],
    *,
    owner: str,
    name: str,
) -> dict[str, Any]:
    """Run a GraphQL query, tolerating partial per-field errors.

    GitHub can return HTTP 200 with *both* a (partial but otherwise valid)
    ``data`` payload and an ``errors`` list — e.g. ``RESOURCE_LIMITS_EXCEEDED``
    on one deeply-nested optional field (like a single PR's CI checks) while
    every other field succeeds. PyGithub's ``graphql_query`` treats *any*
    ``errors`` entry as fatal and raises, which would otherwise discard an
    entire page of issues/PRs over one bad optional field, and dumps the
    *whole* response JSON (potentially megabytes, once GraphQL repeats the
    error once per affected node) into the exception message.

    This recovers the partial ``data`` when it's usable, logging a short
    summary of the errors instead. If ``data`` isn't usable, re-raises with
    a concise summary rather than PyGithub's raw JSON dump.
    """
    try:
        _, response = requester.graphql_query(query, variables)
        return response["data"]
    except GithubException as exc:
        body = exc.data if isinstance(exc.data, dict) else {}
        partial_data = body.get("data")
        raw_errors = body.get("errors")
        errors: list[Any] = raw_errors if isinstance(raw_errors, list) else []
        summary = _summarize_graphql_errors(errors)
        if isinstance(partial_data, dict) and partial_data.get("repository"):
            logger.warning(
                "GraphQL query for %s/%s returned partial errors "
                "(using partial data): %s",
                owner,
                name,
                summary,
            )
            return partial_data
        raise GithubException(exc.status, headers=exc.headers, message=summary) from exc


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
    states: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield normalized issue GraphQL nodes for one repo, following pagination.

    Args:
        requester: ``Github.requester`` — the same authenticated client used
            by every REST call elsewhere in the collector.
        owner: Repository owner/org (e.g. ``"canonical"``).
        name: Repository name (without owner prefix).
        since: Only fetch issues updated on or after this timestamp
            (GraphQL ``filterBy: {since: ...}``). ``None`` fetches all open
            issues.
        states: List of issue states (e.g. ``["OPEN"]`` or ``["OPEN", "CLOSED"]``).
            Defaults to ``["OPEN"]``.

    Yields:
        Raw GraphQL issue node dicts, in updated-at-descending order.

    """
    after: str | None = None
    since_str = (
        since.astimezone(UTC).isoformat().replace("+00:00", "Z") if since else None
    )
    variables: dict[str, Any] = {
        "owner": owner,
        "name": name,
        "after": after,
        "since": since_str,
    }
    if states is not None:
        variables["states"] = states
    while True:
        variables["after"] = after
        data = _graphql_query(
            requester,
            _ISSUES_QUERY,
            variables,
            owner=owner,
            name=name,
        )
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
    states: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield normalized PR GraphQL nodes for one repo, following pagination.

    ``pullRequests`` has no server-side ``since``/``filterBy`` argument (unlike
    ``issues``), so filtering by ``since`` is done client-side against each
    node's ``updatedAt``.

    Args:
        requester: ``Github.requester``.
        owner: Repository owner/org.
        name: Repository name.
        since: Only yield PRs whose ``updatedAt`` is on or after this
            timestamp. ``None`` yields all matching PRs.
        states: List of PR states (e.g. ``["OPEN"]`` or ``["OPEN", "CLOSED", "MERGED"]``).
            Defaults to ``["OPEN"]``.

    Yields:
        Raw GraphQL PR node dicts, in updated-at-descending order.

    """
    after: str | None = None
    variables: dict[str, Any] = {"owner": owner, "name": name, "after": after}
    if states is not None:
        variables["states"] = states
    while True:
        variables["after"] = after
        data = _graphql_query(
            requester,
            _PULL_REQUESTS_QUERY,
            variables,
            owner=owner,
            name=name,
        )
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
                return
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
    raw_tag_nodes: list[dict[str, Any]] = []
    first_page = True

    while True:
        data = _graphql_query(
            requester,
            _RELEASES_AND_BRANCHES_QUERY,
            {"owner": owner, "name": name, "after": after},
            owner=owner,
            name=name,
        )
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
            branch_names = [
                ref["name"] for ref in (repo.get("refs") or {}).get("nodes", [])
            ]
            raw_tag_nodes = (repo.get("tags") or {}).get("nodes", [])
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
            break
        after = page["pageInfo"]["endCursor"]

    # Fall back to git tags for releases missing a formal GitHub release entity.
    # Restrict strictly to 'X.Y.Z' or 'vX.Y.Z' syntax.
    existing_tags = {r["tagName"] for r in releases}
    for tag in raw_tag_nodes:
        tag_name = tag.get("name")
        if not tag_name or not _SEMVER_TAG_REGEX.match(tag_name):
            continue
        if tag_name in existing_tags:
            continue
        tag_date = _extract_tag_date(tag.get("target"))
        created_at = _parse_graphql_datetime(tag_date)
        if (
            known_since is not None
            and created_at is not None
            and created_at <= known_since
        ):
            continue
        releases.append(
            {
                "tagName": tag_name,
                "isPrerelease": False,
                "isDraft": False,
                "createdAt": tag_date,
                "publishedAt": tag_date,
            }
        )
        existing_tags.add(tag_name)

    return releases, branch_names


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
    check_suites = last_commit.get("checkSuites")
    # checkSuites (and nested checkRuns) can come back null — or a dict with
    # a null "nodes" list — if this field hit GitHub's
    # RESOURCE_LIMITS_EXCEEDED error on a heavily-nested query (see
    # _graphql_query's partial-data recovery) — treat as "no CI data".
    if not check_suites or not check_suites.get("nodes"):
        return ci_passing, ci_failing, ci_pending
    for suite in check_suites["nodes"]:
        check_runs = suite.get("checkRuns")
        if not check_runs or not check_runs.get("nodes"):
            continue
        for check in check_runs["nodes"]:
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


def fetch_issue_states(
    requester: "Requester",
    owner: str,
    name: str,
    numbers: list[int],
    *,
    batch_size: int = 50,
) -> dict[int, dict[str, Any]]:
    """Batch fetch states and closed/merged timestamps for a list of issue/PR numbers.

    Constructs dynamic GraphQL queries using aliased ``issueOrPullRequest(number: N)``
    fields to reconcile status of multiple issues in a single request.

    Args:
        requester: ``Github.requester``.
        owner: Repository owner/org.
        name: Repository name.
        numbers: List of issue/PR numbers to look up.
        batch_size: Maximum number of items per GraphQL query.

    Returns:
        A dictionary mapping issue/PR number to a dict containing:
        ``{"state": str, "closed_at": datetime | None, "merged_at": datetime | None}``.

    """
    if not numbers:
        return {}

    results: dict[int, dict[str, Any]] = {}
    for i in range(0, len(numbers), batch_size):
        chunk = numbers[i : i + batch_size]
        fields = [
            f"""
            i{num}: issueOrPullRequest(number: {num}) {{
                ... on Issue {{ state closedAt }}
                ... on PullRequest {{ state closedAt mergedAt }}
            }}
            """
            for num in chunk
        ]
        query = f"""
        query($owner: String!, $name: String!) {{
            rateLimit {{ cost remaining resetAt }}
            repository(owner: $owner, name: $name) {{
                {" ".join(fields)}
            }}
        }}
        """
        data = _graphql_query(
            requester,
            query,
            {"owner": owner, "name": name},
            owner=owner,
            name=name,
        )
        repo_data = data.get("repository", {})
        for num in chunk:
            item = repo_data.get(f"i{num}")
            if not item:
                continue
            raw_state = item.get("state", "").upper()
            state = "closed" if raw_state in ("CLOSED", "MERGED") else "open"
            closed_at_str = item.get("closedAt") or item.get("mergedAt")
            merged_at_str = item.get("mergedAt")
            results[num] = {
                "state": state,
                "closed_at": _parse_graphql_datetime(closed_at_str),
                "merged_at": _parse_graphql_datetime(merged_at_str),
            }

    return results
