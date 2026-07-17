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

__all__ = ["paginated_issues", "GraphQLCost"]

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


class GraphQLCost:
    """Parsed ``rateLimit`` block from a GraphQL response, for budget logging."""

    def __init__(self, cost: int, remaining: int, reset_at: datetime | None) -> None:
        self.cost = cost
        self.remaining = remaining
        self.reset_at = reset_at

    @classmethod
    def from_response(cls, rate_limit: dict[str, Any]) -> "GraphQLCost":
        """Build a cost snapshot from a GraphQL ``rateLimit`` response block."""
        reset_raw = rate_limit.get("resetAt")
        reset_at = (
            datetime.fromisoformat(reset_raw.replace("Z", "+00:00"))
            if reset_raw
            else None
        )
        return cls(
            cost=int(rate_limit["cost"]),
            remaining=int(rate_limit["remaining"]),
            reset_at=reset_at,
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
            "GraphQL issues page for %s/%s: cost=%d remaining=%d",
            owner,
            name,
            cost.cost,
            cost.remaining,
        )
        issues = data["repository"]["issues"]
        yield from issues["nodes"]

        page_info = issues["pageInfo"]
        if not page_info["hasNextPage"]:
            return
        after = page_info["endCursor"]
