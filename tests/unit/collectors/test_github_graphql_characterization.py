"""Characterization tests for GitHub GraphQL pagination behaviour."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from craft_dashboard.collectors.github_graphql import (
    _fetch_hotfix_branch_names,
    _fetch_tag_nodes,
    paginated_issues,
    paginated_pull_requests,
    paginated_releases_and_branches,
)


def _rate_limit() -> dict:
    return {"cost": 1, "remaining": 4999, "resetAt": None}


def _issue_node(number: int) -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": "",
        "state": "OPEN",
        "createdAt": "2025-01-01T00:00:00Z",
        "updatedAt": "2025-01-02T00:00:00Z",
        "closedAt": None,
        "url": f"https://github.com/canonical/repo/issues/{number}",
        "author": {"login": "octocat"},
        "labels": {"nodes": []},
        "comments": {"nodes": []},
        "timelineItems": {"nodes": []},
    }


def _pr_node(number: int, updated_at: str = "2025-01-02T00:00:00Z") -> dict:
    return {
        "number": number,
        "title": f"PR {number}",
        "body": "",
        "state": "OPEN",
        "createdAt": "2025-01-01T00:00:00Z",
        "updatedAt": updated_at,
        "closedAt": None,
        "mergedAt": None,
        "url": f"https://github.com/canonical/repo/pull/{number}",
        "author": {"login": "octocat"},
        "labels": {"nodes": []},
        "additions": 1,
        "deletions": 0,
        "changedFiles": 1,
        "comments": {"nodes": []},
        "reviews": {"nodes": []},
        "reviewThreads": {"nodes": []},
        "commits": {"nodes": []},
    }


def _graphql_page(connection: str, nodes: list[dict], cursor: str | None):
    return (
        {},
        {
            "data": {
                "rateLimit": _rate_limit(),
                "repository": {
                    connection: {
                        "pageInfo": {
                            "hasNextPage": cursor is not None,
                            "endCursor": cursor,
                        },
                        "nodes": nodes,
                    }
                },
            }
        },
    )


def test_issue_pagination_sends_previous_end_cursor_as_next_after() -> None:
    requester = MagicMock()
    pages = [
        _graphql_page("issues", [_issue_node(1)], "ISSUE_CURSOR"),
        _graphql_page("issues", [_issue_node(2)], None),
    ]
    captured_after = []

    def graphql_query(_query, variables):
        captured_after.append(variables["after"])
        return pages.pop(0)

    requester.graphql_query.side_effect = graphql_query

    results = list(paginated_issues(requester, "canonical", "repo"))

    assert [node["number"] for node in results] == [1, 2]
    assert captured_after == [None, "ISSUE_CURSOR"]


def test_issue_pagination_with_null_end_cursor_fetches_next_page_with_null_after() -> (
    None
):
    requester = MagicMock()
    requester.graphql_query.side_effect = [
        (
            {},
            {
                "data": {
                    "rateLimit": _rate_limit(),
                    "repository": {
                        "issues": {
                            "pageInfo": {
                                "hasNextPage": True,
                                "endCursor": None,
                            },
                            "nodes": [_issue_node(1)],
                        }
                    },
                }
            },
        ),
        _graphql_page("issues", [_issue_node(2)], None),
    ]

    results = list(paginated_issues(requester, "canonical", "repo"))

    # NOTE: looks suspect -- hasNextPage=True with endCursor=None causes a
    # second request with after=None, rather than aborting or detecting that
    # pagination cannot advance.
    assert [node["number"] for node in results] == [1, 2]
    assert [
        call.args[1]["after"] for call in requester.graphql_query.call_args_list
    ] == [
        None,
        None,
    ]


def test_pr_pagination_sends_previous_end_cursor_as_next_after() -> None:
    requester = MagicMock()
    pages = [
        _graphql_page("pullRequests", [_pr_node(1)], "PR_CURSOR"),
        _graphql_page("pullRequests", [_pr_node(2)], None),
    ]
    captured_after = []

    def graphql_query(_query, variables):
        captured_after.append(variables["after"])
        return pages.pop(0)

    requester.graphql_query.side_effect = graphql_query

    results = list(paginated_pull_requests(requester, "canonical", "repo"))

    assert [node["number"] for node in results] == [1, 2]
    assert captured_after == [None, "PR_CURSOR"]


def test_pr_pagination_stops_immediately_when_node_is_older_than_since() -> None:
    requester = MagicMock()
    requester.graphql_query.side_effect = [
        _graphql_page(
            "pullRequests",
            [
                _pr_node(1, "2025-01-10T00:00:00Z"),
                _pr_node(2, "2025-01-01T00:00:00Z"),
            ],
            "PR_CURSOR",
        ),
        _graphql_page("pullRequests", [_pr_node(3, "2024-12-31T00:00:00Z")], None),
    ]

    results = list(
        paginated_pull_requests(
            requester,
            "canonical",
            "repo",
            since=datetime(2025, 1, 5, tzinfo=UTC),
        )
    )

    assert [node["number"] for node in results] == [1]
    assert requester.graphql_query.call_count == 1


def test_hotfix_refs_pagination_sends_previous_end_cursor_as_next_after() -> None:
    requester = MagicMock()
    requester.graphql_query.side_effect = [
        _graphql_page("refs", [{"name": "hotfix/1.0"}], "REF_CURSOR"),
        _graphql_page("refs", [{"name": "hotfix/2.0"}], None),
    ]

    branch_names = _fetch_hotfix_branch_names(requester, "canonical", "repo")

    assert branch_names == ["hotfix/1.0", "hotfix/2.0"]
    assert [
        call.args[1]["after"] for call in requester.graphql_query.call_args_list
    ] == [
        None,
        "REF_CURSOR",
    ]


def test_tag_refs_pagination_sends_previous_end_cursor_as_next_after() -> None:
    requester = MagicMock()
    requester.graphql_query.side_effect = [
        _graphql_page(
            "tags",
            [{"name": "1.0.0", "target": {"committedDate": "2025-01-02T00:00:00Z"}}],
            "TAG_CURSOR",
        ),
        _graphql_page(
            "tags",
            [{"name": "2.0.0", "target": {"committedDate": "2025-01-01T00:00:00Z"}}],
            None,
        ),
    ]

    tag_nodes = _fetch_tag_nodes(requester, "canonical", "repo", known_since=None)

    assert [node["name"] for node in tag_nodes] == ["1.0.0", "2.0.0"]
    assert [
        call.args[1]["after"] for call in requester.graphql_query.call_args_list
    ] == [
        None,
        "TAG_CURSOR",
    ]


def test_tag_refs_pagination_includes_cutoff_tag_before_stopping() -> None:
    requester = MagicMock()
    requester.graphql_query.side_effect = [
        _graphql_page(
            "tags",
            [
                {
                    "name": "newer",
                    "target": {"committedDate": "2025-01-10T00:00:00Z"},
                },
                {
                    "name": "cutoff",
                    "target": {"committedDate": "2025-01-05T00:00:00Z"},
                },
            ],
            "TAG_CURSOR",
        ),
        _graphql_page(
            "tags",
            [{"name": "older", "target": {"committedDate": "2025-01-01T00:00:00Z"}}],
            None,
        ),
    ]

    tag_nodes = _fetch_tag_nodes(
        requester,
        "canonical",
        "repo",
        known_since=datetime(2025, 1, 5, tzinfo=UTC),
    )

    assert [node["name"] for node in tag_nodes] == ["newer", "cutoff"]
    assert requester.graphql_query.call_count == 1


def test_releases_and_branches_fetches_releases_then_all_refs_connections() -> None:
    requester = MagicMock()
    requester.graphql_query.side_effect = [
        _graphql_page(
            "releases",
            [
                {
                    "tagName": "v1.0.0",
                    "isPrerelease": False,
                    "isDraft": False,
                    "createdAt": "2025-01-10T00:00:00Z",
                    "publishedAt": "2025-01-10T00:00:00Z",
                }
            ],
            None,
        ),
        _graphql_page("refs", [{"name": "hotfix/1.0"}], None),
        _graphql_page(
            "tags",
            [{"name": "v2.0.0", "target": {"committedDate": "2025-01-11T00:00:00Z"}}],
            None,
        ),
    ]

    releases, branch_names = paginated_releases_and_branches(
        requester,
        "canonical",
        "repo",
        known_since=None,
    )

    assert [release["tagName"] for release in releases] == ["v1.0.0", "v2.0.0"]
    assert branch_names == ["hotfix/1.0"]
    query_texts = [call.args[0] for call in requester.graphql_query.call_args_list]
    assert "releases(first: 20" in query_texts[0]
    assert 'refs(refPrefix: "refs/heads/", query: "hotfix/"' in query_texts[1]
    assert 'tags: refs(refPrefix: "refs/tags/"' in query_texts[2]
