"""Tests for the GitHub GraphQL query/pagination layer."""

from unittest.mock import MagicMock

from craft_dashboard.collectors.github_graphql import paginated_issues


def _issue_node(number: int, updated_at: str = "2025-01-10T12:00:00Z") -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": "body text",
        "state": "OPEN",
        "createdAt": "2025-01-01T00:00:00Z",
        "updatedAt": updated_at,
        "closedAt": None,
        "url": f"https://github.com/canonical/repo/issues/{number}",
        "author": {"login": "octocat"},
        "labels": {"nodes": [{"name": "bug"}]},
        "comments": {"nodes": []},
        "timelineItems": {"nodes": []},
    }


class TestPaginatedIssues:
    def test_single_page_yields_all_nodes(self) -> None:
        requester = MagicMock()
        requester.graphql_query.return_value = (
            {},
            {
                "data": {
                    "rateLimit": {
                        "cost": 1,
                        "remaining": 4999,
                        "resetAt": "2025-01-10T13:00:00Z",
                    },
                    "repository": {
                        "issues": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [_issue_node(1), _issue_node(2)],
                        }
                    },
                }
            },
        )

        results = list(
            paginated_issues(requester, owner="canonical", name="repo", since=None)
        )

        assert [node["number"] for node in results] == [1, 2]
        requester.graphql_query.assert_called_once()

    def test_follows_pagination_cursor_across_pages(self) -> None:
        requester = MagicMock()
        requester.graphql_query.side_effect = [
            (
                {},
                {
                    "data": {
                        "rateLimit": {"cost": 1, "remaining": 4999, "resetAt": None},
                        "repository": {
                            "issues": {
                                "pageInfo": {
                                    "hasNextPage": True,
                                    "endCursor": "CURSOR1",
                                },
                                "nodes": [_issue_node(1)],
                            }
                        },
                    }
                },
            ),
            (
                {},
                {
                    "data": {
                        "rateLimit": {"cost": 1, "remaining": 4998, "resetAt": None},
                        "repository": {
                            "issues": {
                                "pageInfo": {
                                    "hasNextPage": False,
                                    "endCursor": None,
                                },
                                "nodes": [_issue_node(2)],
                            }
                        },
                    }
                },
            ),
        ]

        results = list(
            paginated_issues(requester, owner="canonical", name="repo", since=None)
        )

        assert [node["number"] for node in results] == [1, 2]
        assert requester.graphql_query.call_count == 2
        second_call_variables = requester.graphql_query.call_args_list[1].args[1]
        assert second_call_variables["after"] == "CURSOR1"
