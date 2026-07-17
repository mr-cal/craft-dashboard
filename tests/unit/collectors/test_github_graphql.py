"""Tests for the GitHub GraphQL query/pagination layer."""

import logging
from datetime import UTC, datetime
from unittest.mock import MagicMock

from craft_dashboard.collectors.github_graphql import (
    classify_pr_ci_checks,
    classify_pr_review_status,
    paginated_issues,
    paginated_pull_requests,
    paginated_releases_and_branches,
)


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
                                _pr_node(1, updated_at="2025-01-05T00:00:00Z"),
                                _pr_node(2, updated_at="2025-01-10T12:00:00Z"),
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
