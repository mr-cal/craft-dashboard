"""Tests for the GitHub GraphQL query/pagination layer."""

import logging
import re
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from craft_dashboard.collectors.github_graphql import (
    _ISSUES_QUERY,
    _PULL_REQUESTS_QUERY,
    _RELEASES_AND_BRANCHES_QUERY,
    _graphql_query,
    _summarize_graphql_errors,
    classify_pr_ci_checks,
    classify_pr_review_status,
    paginated_issues,
    paginated_pull_requests,
    paginated_releases_and_branches,
)
from github import GithubException


def _extract_pagination_arguments(query: str) -> list[tuple[str, str, int]]:
    return [
        (field_name, direction, int(value))
        for field_name, direction, value in re.findall(
            r"(\w+)\([^)]*\b(first|last):\s*(\d+)", query
        )
    ]


def _extract_pagination_limits(query: str) -> dict[str, int]:
    return {
        field_name: value
        for field_name, _, value in _extract_pagination_arguments(query)
    }


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
    def test_logs_full_rate_limit_block(self, caplog) -> None:
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
                            "nodes": [_issue_node(1)],
                        }
                    },
                }
            },
        )

        with caplog.at_level(
            logging.DEBUG, logger="craft_dashboard.collectors.github_graphql"
        ):
            results = list(
                paginated_issues(requester, owner="canonical", name="repo", since=None)
            )

        assert [node["number"] for node in results] == [1]
        assert "reset_at=2025-01-10 13:00:00+00:00" in caplog.records[0].message

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


def _pr_node(number: int, updated_at: str = "2025-01-10T12:00:00Z") -> dict:
    return {
        "number": number,
        "title": f"PR {number}",
        "body": "body text",
        "state": "OPEN",
        "createdAt": "2025-01-01T00:00:00Z",
        "updatedAt": updated_at,
        "closedAt": None,
        "mergedAt": None,
        "url": f"https://github.com/canonical/repo/pull/{number}",
        "author": {"login": "octocat"},
        "labels": {"nodes": []},
        "additions": 10,
        "deletions": 2,
        "changedFiles": 3,
        "comments": {"nodes": []},
        "reviews": {"nodes": [{"author": {"login": "reviewer1"}, "state": "APPROVED"}]},
        "reviewThreads": {"nodes": []},
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "checkSuites": {
                            "nodes": [
                                {
                                    "checkRuns": {
                                        "nodes": [
                                            {
                                                "name": "ci",
                                                "conclusion": "SUCCESS",
                                                "status": "COMPLETED",
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            ]
        },
    }


def _release_node(tag: str, created_at: str) -> dict:
    return {
        "tagName": tag,
        "isPrerelease": False,
        "isDraft": False,
        "createdAt": created_at,
        "publishedAt": created_at,
    }


class TestPaginatedPullRequests:
    def test_filters_client_side_by_since(self) -> None:
        requester = MagicMock()
        requester.graphql_query.return_value = (
            {},
            {
                "data": {
                    "rateLimit": {"cost": 7, "remaining": 4990, "resetAt": None},
                    "repository": {
                        "pullRequests": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                _pr_node(2, updated_at="2025-01-10T12:00:00Z"),
                                _pr_node(1, updated_at="2025-01-05T00:00:00Z"),
                            ],
                        }
                    },
                }
            },
        )

        results = list(
            paginated_pull_requests(
                requester,
                owner="canonical",
                name="repo",
                since=datetime(2025, 1, 9, tzinfo=UTC),
            )
        )

        assert [node["number"] for node in results] == [2]

    def test_stops_paginating_after_first_pr_older_than_since(self) -> None:
        requester = MagicMock()
        requester.graphql_query.side_effect = [
            (
                {},
                {
                    "data": {
                        "rateLimit": {"cost": 7, "remaining": 4990, "resetAt": None},
                        "repository": {
                            "pullRequests": {
                                "pageInfo": {"hasNextPage": True, "endCursor": "CUR1"},
                                "nodes": [
                                    _pr_node(2, updated_at="2025-01-10T12:00:00Z"),
                                    _pr_node(1, updated_at="2025-01-05T00:00:00Z"),
                                ],
                            }
                        },
                    }
                },
            ),
            (
                {},
                {
                    "data": {
                        "rateLimit": {"cost": 7, "remaining": 4983, "resetAt": None},
                        "repository": {
                            "pullRequests": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [
                                    _pr_node(0, updated_at="2025-01-01T00:00:00Z")
                                ],
                            }
                        },
                    }
                },
            ),
        ]

        results = list(
            paginated_pull_requests(
                requester,
                owner="canonical",
                name="repo",
                since=datetime(2025, 1, 9, tzinfo=UTC),
            )
        )

        assert [node["number"] for node in results] == [2]
        requester.graphql_query.assert_called_once()


class TestPaginatedReleasesAndBranches:
    def test_returns_releases_and_hotfix_branch_names(self) -> None:
        requester = MagicMock()
        requester.graphql_query.return_value = (
            {},
            {
                "data": {
                    "rateLimit": {"cost": 2, "remaining": 4995, "resetAt": None},
                    "repository": {
                        "releases": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                _release_node("v2.0.0", "2025-01-10T00:00:00Z"),
                                _release_node("v1.0.0", "2025-01-01T00:00:00Z"),
                            ],
                        },
                        "refs": {
                            "nodes": [{"name": "hotfix/1.0"}, {"name": "hotfix/2.0"}]
                        },
                    },
                }
            },
        )

        releases, branch_names = paginated_releases_and_branches(
            requester, owner="canonical", name="repo", known_since=None
        )

        assert [r["tagName"] for r in releases] == ["v2.0.0", "v1.0.0"]
        assert branch_names == ["hotfix/1.0", "hotfix/2.0"]

    def test_stops_paginating_once_known_release_reached(self) -> None:
        requester = MagicMock()
        requester.graphql_query.side_effect = [
            (
                {},
                {
                    "data": {
                        "rateLimit": {"cost": 2, "remaining": 4995, "resetAt": None},
                        "repository": {
                            "releases": {
                                "pageInfo": {
                                    "hasNextPage": True,
                                    "endCursor": "CUR1",
                                },
                                "nodes": [
                                    _release_node("v3.0.0", "2025-01-15T00:00:00Z"),
                                    _release_node("v2.0.0", "2025-01-10T00:00:00Z"),
                                ],
                            },
                            "refs": {"nodes": []},
                        },
                    }
                },
            )
        ]

        releases, _ = paginated_releases_and_branches(
            requester,
            owner="canonical",
            name="repo",
            known_since=datetime(2025, 1, 12, tzinfo=UTC),
        )

        assert [r["tagName"] for r in releases] == ["v3.0.0"]
        assert requester.graphql_query.call_count == 1


class TestClassifyPrReviewStatus:
    def test_changes_requested_wins_over_approval(self) -> None:
        reviews = [
            {"author": {"login": "alice"}, "state": "APPROVED"},
            {"author": {"login": "bob"}, "state": "CHANGES_REQUESTED"},
        ]

        status, count = classify_pr_review_status(reviews)

        assert status == "changes_requested"
        assert count == 2

    def test_all_approved(self) -> None:
        reviews = [{"author": {"login": "alice"}, "state": "APPROVED"}]

        status, count = classify_pr_review_status(reviews)

        assert status == "approved"
        assert count == 1

    def test_no_reviews_is_pending(self) -> None:
        status, count = classify_pr_review_status([])

        assert status == "pending"
        assert count == 0


class TestClassifyPrCiChecks:
    def test_classifies_passing_failing_pending(self) -> None:
        commits = [
            {
                "commit": {
                    "checkSuites": {
                        "nodes": [
                            {
                                "checkRuns": {
                                    "nodes": [
                                        {
                                            "name": "unit",
                                            "conclusion": "SUCCESS",
                                            "status": "COMPLETED",
                                        },
                                        {
                                            "name": "lint",
                                            "conclusion": "FAILURE",
                                            "status": "COMPLETED",
                                        },
                                        {
                                            "name": "deploy",
                                            "conclusion": None,
                                            "status": "IN_PROGRESS",
                                        },
                                    ]
                                }
                            }
                        ]
                    }
                }
            }
        ]

        passing, failing, pending = classify_pr_ci_checks(commits)

        assert passing == ["unit"]
        assert failing == ["lint"]
        assert pending == ["deploy"]

    def test_no_commits_returns_empty_lists(self) -> None:
        assert classify_pr_ci_checks([]) == ([], [], [])

    def test_null_check_suites_returns_empty_lists(self) -> None:
        """A node hit by RESOURCE_LIMITS_EXCEEDED may have checkSuites: None."""
        commits = [{"commit": {"checkSuites": None}}]

        assert classify_pr_ci_checks(commits) == ([], [], [])

    def test_null_check_runs_returns_empty_lists(self) -> None:
        commits = [{"commit": {"checkSuites": {"nodes": [{"checkRuns": None}]}}}]

        assert classify_pr_ci_checks(commits) == ([], [], [])


class TestSummarizeGraphqlErrors:
    def test_dedupes_and_joins_messages(self) -> None:
        errors = [
            {"type": "RESOURCE_LIMITS_EXCEEDED", "message": "Resource limits."},
            {"type": "RESOURCE_LIMITS_EXCEEDED", "message": "Resource limits."},
            {"type": "OTHER", "message": "Something else."},
        ]

        summary = _summarize_graphql_errors(errors)

        assert summary == (
            "RESOURCE_LIMITS_EXCEEDED: Resource limits.; OTHER: Something else."
        )

    def test_truncates_long_summary(self) -> None:
        errors = [{"type": "E", "message": "x" * 1000}]

        summary = _summarize_graphql_errors(errors)

        assert len(summary) <= 520
        assert summary.endswith("... (truncated)")

    def test_empty_errors_returns_unknown(self) -> None:
        assert _summarize_graphql_errors([]) == "unknown error"


class TestGraphqlQueryPartialRecovery:
    def test_recovers_partial_data_when_repository_present(self, caplog) -> None:
        requester = MagicMock()
        partial_body = {
            "data": {"repository": {"issues": {"nodes": []}}},
            "errors": [
                {"type": "RESOURCE_LIMITS_EXCEEDED", "message": "Resource limits."}
            ],
        }
        requester.graphql_query.side_effect = GithubException(
            400, data=partial_body, headers={}
        )

        with caplog.at_level(logging.WARNING):
            result = _graphql_query(
                requester, _ISSUES_QUERY, {}, owner="canonical", name="rockcraft"
            )

        assert result == partial_body["data"]
        assert "RESOURCE_LIMITS_EXCEEDED" in caplog.text
        # The raw megabyte-scale error body must not leak into the logs.
        assert "Resource limits." in caplog.text

    def test_raises_concise_error_when_data_unusable(self) -> None:
        requester = MagicMock()
        body = {
            "data": None,
            "errors": [{"type": "SOME_ERROR", "message": "Totally broken."}],
        }
        requester.graphql_query.side_effect = GithubException(
            400, data=body, headers={}
        )

        with pytest.raises(GithubException) as exc_info:
            _graphql_query(
                requester, _ISSUES_QUERY, {}, owner="canonical", name="rockcraft"
            )

        assert exc_info.value.data is None
        assert "SOME_ERROR: Totally broken." in str(exc_info.value)
        # No raw JSON dump of the (potentially huge) response body.
        assert "Totally broken." in str(exc_info.value)
        assert str(exc_info.value).count("Totally broken.") == 1

    def test_returns_data_directly_on_success(self) -> None:
        requester = MagicMock()
        requester.graphql_query.return_value = (
            {},
            {"data": {"repository": {"issues": {"nodes": []}}}},
        )

        result = _graphql_query(
            requester, _ISSUES_QUERY, {}, owner="canonical", name="rockcraft"
        )

        assert result == {"repository": {"issues": {"nodes": []}}}


class TestNodeLimits:
    def test_issues_query_stays_under_github_node_limit(self) -> None:
        sizes = _extract_pagination_limits(_ISSUES_QUERY)

        # issues(first: 50) with child connections:
        # - labels(first: 20) -> 20
        # - comments(last: 10) -> 10
        # - timelineItems(last: 20) -> 20
        # Per issue = 1 issue node + 20 + 10 + 20 = 51.
        # Per page = 50 * 51 = 2,550 nodes.
        max_nodes_per_page = sizes["issues"] * (
            1 + sizes["labels"] + sizes["comments"] + sizes["timelineItems"]
        )

        assert max_nodes_per_page == 2_550
        assert max_nodes_per_page < 500_000

    def test_pull_requests_query_stays_under_github_node_limit(self) -> None:
        sizes = _extract_pagination_limits(_PULL_REQUESTS_QUERY)

        # pullRequests(first: 50) with child connections:
        # - labels(first: 20) -> 20
        # - comments(last: 10) -> 10
        # - reviews(last: 20) -> 20
        # - reviewThreads(first: 50) -> 50
        # - commits(last: 1) -> checkSuites(first: 10) -> checkRuns(first: 20)
        #   contributes 1 commit node + (1 * 10) checkSuites nodes
        #   + (1 * 10 * 20) checkRuns nodes = 211 nodes.
        # Per PR = 1 PR node + 20 + 10 + 20 + 50 + 211 = 312.
        # Per page = 50 * 312 = 15,600 nodes.
        max_nodes_per_page = sizes["pullRequests"] * (
            1
            + sizes["labels"]
            + sizes["comments"]
            + sizes["reviews"]
            + sizes["reviewThreads"]
            + sizes["commits"]
            + sizes["commits"] * sizes["checkSuites"]
            + sizes["commits"] * sizes["checkSuites"] * sizes["checkRuns"]
        )

        assert max_nodes_per_page == 15_600
        assert max_nodes_per_page < 500_000

    def test_releases_and_branches_query_stays_under_github_node_limit(self) -> None:
        sizes = _extract_pagination_limits(_RELEASES_AND_BRANCHES_QUERY)

        # This query has two top-level sibling connections under repository:
        # - releases(first: 20) with no nested connections -> 20 * 1 = 20
        # - refs(..., first: 100) with no nested connections -> 100 * 1 = 100
        # Total worst case per query page = 20 + 100 = 120 nodes.
        max_nodes_per_page = sizes["releases"] + sizes["refs"]

        assert max_nodes_per_page == 120
        assert max_nodes_per_page < 500_000

    def test_queries_lock_in_all_page_size_arguments(self) -> None:
        assert _extract_pagination_limits(_ISSUES_QUERY) == {
            "issues": 50,
            "labels": 20,
            "comments": 10,
            "timelineItems": 20,
        }

        assert _extract_pagination_arguments(_ISSUES_QUERY) == [
            ("issues", "first", 50),
            ("labels", "first", 20),
            ("comments", "last", 10),
            ("timelineItems", "last", 20),
        ]

        assert _extract_pagination_limits(_PULL_REQUESTS_QUERY) == {
            "pullRequests": 50,
            "labels": 20,
            "comments": 10,
            "reviews": 20,
            "reviewThreads": 50,
            "commits": 1,
            "checkSuites": 10,
            "checkRuns": 20,
        }

        assert _extract_pagination_arguments(_PULL_REQUESTS_QUERY) == [
            ("pullRequests", "first", 50),
            ("labels", "first", 20),
            ("comments", "last", 10),
            ("reviews", "last", 20),
            ("reviewThreads", "first", 50),
            ("commits", "last", 1),
            ("checkSuites", "first", 10),
            ("checkRuns", "first", 20),
        ]

        assert _extract_pagination_limits(_RELEASES_AND_BRANCHES_QUERY) == {
            "releases": 20,
            "refs": 100,
        }

        assert _extract_pagination_arguments(_RELEASES_AND_BRANCHES_QUERY) == [
            ("releases", "first", 20),
            ("refs", "first", 100),
        ]
