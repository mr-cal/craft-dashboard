"""Adapters presenting GraphQL issue and PR nodes as PyGithub-shaped objects."""

from collections import deque
from collections.abc import Iterable, Iterator
from types import SimpleNamespace
from typing import Any

from craft_dashboard.collectors.github_graphql import (
    _parse_graphql_datetime,
    classify_pr_ci_checks,
    classify_pr_review_status,
)


def comments_from_graphql_node(node: dict[str, Any]) -> list[dict]:
    """Convert a GraphQL node's embedded ``comments`` to the REST comment shape."""
    return [
        {
            "author": (comment["author"] or {}).get("login", "unknown"),
            "body": (comment["body"] or "")[:1000],
            "created_at": comment["createdAt"],
            "type": "comment",
        }
        # `comments`/`nodes` can come back null on a partial GraphQL error for
        # this field — see the matching guard in GraphQLIssueAdapter.
        for comment in (node.get("comments") or {}).get("nodes") or []
    ]


def closing_refs_from_graphql_node(node: dict[str, Any]) -> list[dict]:
    """Convert a GraphQL issue node's timeline items to the REST closing-ref shape."""
    refs = []
    for item in (node.get("timelineItems") or {}).get("nodes") or []:
        source = item.get("source")
        if source and source.get("mergedAt") is not None:
            refs.append(
                {
                    "type": "pull_request",
                    "number": source["number"],
                    "title": source["title"],
                    "url": source["url"],
                    "state": "merged",
                    "merged_at": source["mergedAt"],
                }
            )
    return refs


class GraphQLIssueAdapter:
    """GraphQL issue shim matching the PyGithub Issue read interface."""

    def __init__(self, node: dict[str, Any]) -> None:
        self._node = node
        self.number = node["number"]
        self.title = node["title"]
        self.body = node["body"]
        self.state = node["state"].lower()
        author = node["author"]
        self.user = SimpleNamespace(login=author["login"]) if author else None
        # `labels` (or its nested `nodes`) can come back null on a partial
        # GraphQL error for this field (e.g. RESOURCE_LIMITS_EXCEEDED on a
        # deeply-nested query) — see `classify_pr_ci_checks`'s similar guard
        # for `checkSuites`. Treat as "no labels" rather than crashing.
        label_nodes = (node.get("labels") or {}).get("nodes") or []
        self.labels = [SimpleNamespace(name=label["name"]) for label in label_nodes]
        self.created_at = _parse_graphql_datetime(node["createdAt"])
        self.updated_at = _parse_graphql_datetime(node["updatedAt"])
        self.closed_at = _parse_graphql_datetime(node["closedAt"])
        self.html_url = node["url"]
        self.pull_request = None

    def fetch_comments(self) -> list[dict]:
        """Return the last-10 comments already embedded in the GraphQL node."""
        return comments_from_graphql_node(self._node)

    def fetch_closing_references(self) -> list[dict]:
        """Return closing PR references already embedded in the GraphQL node."""
        return closing_refs_from_graphql_node(self._node)


class GraphQLPullRequestAdapter:
    """GraphQL pull-request shim matching the PyGithub Issue read interface."""

    def __init__(self, node: dict[str, Any]) -> None:
        self._node = node
        self.number = node["number"]
        self.title = node["title"]
        self.body = node["body"]
        self.state = node["state"].lower()
        author = node["author"]
        self.user = SimpleNamespace(login=author["login"]) if author else None
        # See the matching comment in GraphQLIssueAdapter.__init__.
        label_nodes = (node.get("labels") or {}).get("nodes") or []
        self.labels = [SimpleNamespace(name=label["name"]) for label in label_nodes]
        self.created_at = _parse_graphql_datetime(node["createdAt"])
        self.updated_at = _parse_graphql_datetime(node["updatedAt"])
        self.closed_at = _parse_graphql_datetime(node["closedAt"])
        self.html_url = node["url"]
        self.pull_request = SimpleNamespace(
            merged_at=_parse_graphql_datetime(node["mergedAt"])
        )

    def fetch_comments(self) -> list[dict]:
        """Return the last-10 comments already embedded in the GraphQL node."""
        return comments_from_graphql_node(self._node)

    def fetch_pr_details(self) -> dict:
        """Return PR review/CI/diff details already embedded in the GraphQL node."""
        review_status, review_count = classify_pr_review_status(
            (self._node.get("reviews") or {}).get("nodes") or []
        )
        ci_passing, ci_failing, ci_pending = classify_pr_ci_checks(
            (self._node.get("commits") or {}).get("nodes") or []
        )
        unresolved = sum(
            1
            for thread in (self._node.get("reviewThreads") or {}).get("nodes") or []
            if not thread["isResolved"]
        )
        return {
            "review_status": review_status,
            "review_count": review_count,
            "unresolved_review_comments": unresolved,
            "ci_passing": ci_passing,
            "ci_failing": ci_failing,
            "ci_pending": ci_pending,
            "diff_additions": self._node["additions"],
            "diff_deletions": self._node["deletions"],
            "diff_files_changed": self._node["changedFiles"],
        }


def interleave_open_graphql_items(
    issues: Iterable[dict[str, Any]],
    pull_requests: Iterable[dict[str, Any]],
) -> Iterator[GraphQLIssueAdapter | GraphQLPullRequestAdapter]:
    """Yield open GraphQL issues and PRs in round-robin order."""
    active = deque(
        [
            iter(GraphQLIssueAdapter(node) for node in issues),
            iter(GraphQLPullRequestAdapter(node) for node in pull_requests),
        ]
    )
    while active:
        current = active.popleft()
        try:
            yield next(current)
        except StopIteration:
            continue
        active.append(current)
