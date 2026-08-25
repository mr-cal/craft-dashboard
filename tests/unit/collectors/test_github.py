"""Tests for the GitHub data collector."""

from datetime import UTC, datetime
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
import urllib3
from craft_dashboard.collectors.github import (
    GitHubCollector,
    _classify_issue,
    _compute_issue_hash,
    _fetch_issue_comments,
    _fetch_pr_details,
)
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.issue_activity import IssueActivity
from github import GithubException
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

from tests.factories import make_issue, make_project

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"

_TEST_TOKEN = "ghp_test"


class TestClassifyIssue:
    """Tests for _classify_issue helper."""

    def test_classify_open_issue(self) -> None:
        """Open issue with no pull_request attribute is classified as 'issue'."""
        gh_issue = MagicMock()
        gh_issue.pull_request = None
        gh_issue.state = "open"

        result = _classify_issue(gh_issue)

        assert result == ("issue", "open")

    def test_classify_open_pr(self) -> None:
        """Open issue with pull_request attribute is classified as 'pull_request'."""
        gh_issue = MagicMock()
        gh_issue.pull_request = MagicMock()
        gh_issue.state = "open"

        result = _classify_issue(gh_issue)

        assert result == ("pull_request", "open")

    def test_classify_closed_issue(self) -> None:
        """Closed issue is classified with state 'closed'."""
        gh_issue = MagicMock()
        gh_issue.pull_request = None
        gh_issue.state = "closed"

        result = _classify_issue(gh_issue)

        assert result == ("issue", "closed")

    def test_classify_merged_pr(self) -> None:
        """Closed PR with merged_at is classified as 'merged'."""
        gh_issue = MagicMock()
        gh_issue.pull_request = MagicMock()
        gh_issue.pull_request.merged_at = datetime(2024, 1, 1, tzinfo=UTC)
        gh_issue.state = "closed"

        result = _classify_issue(gh_issue)

        assert result == ("pull_request", "merged")


class TestComputeIssueHash:
    """Tests for _compute_issue_hash."""

    def test_same_content_same_hash(self) -> None:
        """Identical content produces identical hashes."""
        hash1 = _compute_issue_hash(
            "snapcraft pack fails with LXD backend on Ubuntu 24.04",
            "When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
            "open",
            ["bug", "priority-high"],
        )
        hash2 = _compute_issue_hash(
            "snapcraft pack fails with LXD backend on Ubuntu 24.04",
            "When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
            "open",
            ["bug", "priority-high"],
        )

        assert hash1 == hash2

    def test_different_content_different_hash(self) -> None:
        """Different content produces different hashes."""
        hash1 = _compute_issue_hash(
            "snapcraft pack fails with LXD backend on Ubuntu 24.04",
            "When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
            "open",
            ["bug", "priority-high"],
        )
        hash2 = _compute_issue_hash(
            "charmcraft deploy times out on large bundles",
            "Deploying a large bundle stalls while charmcraft waits for the controller response.",
            "open",
            ["needs-triage"],
        )

        assert hash1 != hash2

    def test_hash_is_hex_string(self) -> None:
        """Hash is a 64-character hex string (SHA-256)."""
        result = _compute_issue_hash(
            "Add support for core24 base",
            "Please add support for `base: core24` so new snaps can target Ubuntu 24.04.",
            "open",
            ["enhancement", "snapcraft"],
        )

        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


class TestGitHubCollector:
    """Tests for GitHubCollector."""

    def test_init(self) -> None:
        """GitHubCollector initializes with a token and org."""
        collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")

        assert collector.org == "canonical"

    def test_init_configures_timeout_and_retry(self) -> None:
        """GitHubCollector configures PyGithub timeout and retries."""
        with patch("craft_dashboard.collectors.github.Github") as mock_github:
            GitHubCollector(token=_TEST_TOKEN, org="canonical")

        mock_github.assert_called_once_with(auth=ANY, timeout=30, retry=ANY)
        retry = mock_github.call_args.kwargs["retry"]
        assert isinstance(retry, urllib3.Retry)
        assert retry.total == 3
        assert retry.backoff_factor == 1
        assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}

    def test_is_maintainer_checks_list(self) -> None:
        """is_maintainer checks against the maintainer list."""
        collector = GitHubCollector(
            token=_TEST_TOKEN,
            org="canonical",
            maintainers=["mr-cal", "lengau"],
        )

        assert collector.is_maintainer("mr-cal") is True
        assert collector.is_maintainer("craft-contributor") is False

    def test_is_maintainer_empty_list(self) -> None:
        """is_maintainer returns False when no maintainers configured."""
        collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")

        assert collector.is_maintainer("anyone") is False


class TestFetchIssueComments:
    """Tests for _fetch_issue_comments."""

    def test_returns_last_10_comments(self) -> None:
        """Returns at most 10 comments, most recent last."""
        mock_comment = MagicMock()
        mock_comment.user.login = "craft-contributor"
        mock_comment.body = (
            "Still fails on core24 when using `snapcraft pack --use-lxd`."
        )
        mock_comment.created_at = datetime(2024, 3, 1, tzinfo=UTC)

        gh_issue = MagicMock()
        gh_issue.get_comments.return_value = [mock_comment] * 15

        result = _fetch_issue_comments(gh_issue)

        assert len(result) == 10
        assert result[0]["author"] == "craft-contributor"
        assert (
            result[0]["body"]
            == "Still fails on core24 when using `snapcraft pack --use-lxd`."
        )
        assert result[0]["created_at"] == "2024-03-01T00:00:00+00:00"
        assert result[0]["type"] == "comment"

    def test_truncates_long_body(self) -> None:
        """Comment bodies are truncated to 1000 chars."""
        mock_comment = MagicMock()
        mock_comment.user.login = "sergio-cazzolato"
        mock_comment.body = "x" * 2000
        mock_comment.created_at = datetime(2024, 3, 1, tzinfo=UTC)

        gh_issue = MagicMock()
        gh_issue.get_comments.return_value = [mock_comment]

        result = _fetch_issue_comments(gh_issue)

        assert len(result[0]["body"]) == 1000

    def test_handles_no_user(self) -> None:
        """Comments with no user (deleted accounts) get author 'unknown'."""
        mock_comment = MagicMock()
        mock_comment.user = None
        mock_comment.body = "hello"
        mock_comment.created_at = datetime(2024, 3, 1, tzinfo=UTC)

        gh_issue = MagicMock()
        gh_issue.get_comments.return_value = [mock_comment]

        result = _fetch_issue_comments(gh_issue)

        assert result[0]["author"] == "unknown"

    def test_empty_comments(self) -> None:
        """Returns empty list when there are no comments."""
        gh_issue = MagicMock()
        gh_issue.get_comments.return_value = []

        result = _fetch_issue_comments(gh_issue)

        assert result == []


class TestCollectIssuesExceptionHandling:
    """Tests for collect_issues() exception handling."""

    @staticmethod
    def _make_issue(*, is_pr: bool = False) -> MagicMock:
        label = MagicMock()
        label.name = "bug"

        user = MagicMock()
        user.login = "craft-contributor"

        gh_issue = MagicMock()
        gh_issue.number = 123
        gh_issue.title = "snapcraft pack fails with LXD backend on Ubuntu 24.04"
        gh_issue.body = "When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error."
        gh_issue.state = "open"
        gh_issue.user = user
        gh_issue.labels = [label]
        gh_issue.created_at = datetime(2024, 1, 1, tzinfo=UTC)
        gh_issue.updated_at = datetime(2024, 1, 2, tzinfo=UTC)
        gh_issue.closed_at = None
        gh_issue.html_url = "https://github.com/canonical/snapcraft/issues/123"
        gh_issue.pull_request = MagicMock() if is_pr else None
        return gh_issue

    @staticmethod
    def _make_session() -> AsyncMock:
        due_count_result = MagicMock()
        due_count_result.scalar_one.return_value = 1

        total_count_result = MagicMock()
        total_count_result.scalar_one.return_value = 1

        oldest_fetch_result = MagicMock()
        oldest_fetch_result.scalar_one_or_none.return_value = None  # fresh project

        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = None
        existing_result.one_or_none.return_value = None

        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[
                due_count_result,
                total_count_result,
                oldest_fetch_result,
                existing_result,
                None,
            ]
        )
        session.add = MagicMock()
        session.commit = AsyncMock()
        return session

    @staticmethod
    def _fake_insert(_table) -> MagicMock:
        stmt = MagicMock()
        stmt.excluded = MagicMock()
        stmt.values.return_value = stmt
        stmt.on_conflict_do_update.return_value = stmt
        return stmt

    async def test_collect_issues_catches_github_exception_fetching_comments(
        self, mocker
    ) -> None:
        collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")
        gh_issue = self._make_issue()
        repo = MagicMock()
        repo.get_issues.return_value = [gh_issue]
        collector.gh = MagicMock()
        collector.gh.get_repo.return_value = repo
        session = self._make_session()

        mocker.patch(
            "craft_dashboard.collectors.github._fetch_issue_comments",
            side_effect=GithubException(500, {"message": "boom"}),
        )
        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=self._fake_insert,
        )

        count = await collector.collect_issues("repo", 1, session, state="all")

        assert count == 1
        session.commit.assert_awaited_once()

    async def test_collect_issues_propagates_non_github_exception_fetching_comments(
        self, mocker
    ) -> None:
        collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")
        gh_issue = self._make_issue()
        repo = MagicMock()
        repo.get_issues.return_value = [gh_issue]
        collector.gh = MagicMock()
        collector.gh.get_repo.return_value = repo
        session = self._make_session()

        mocker.patch(
            "craft_dashboard.collectors.github._fetch_issue_comments",
            side_effect=RuntimeError("boom"),
        )
        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=self._fake_insert,
        )

        with pytest.raises(RuntimeError, match="boom"):
            await collector.collect_issues("repo", 1, session, state="all")

    async def test_collect_issues_catches_github_exception_fetching_pr_details(
        self, mocker
    ) -> None:
        collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")
        gh_issue = self._make_issue(is_pr=True)
        repo = MagicMock()
        repo.get_issues.return_value = [gh_issue]
        repo.get_pull.side_effect = GithubException(500, {"message": "boom"})
        collector.gh = MagicMock()
        collector.gh.get_repo.return_value = repo
        session = self._make_session()

        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=self._fake_insert,
        )

        count = await collector.collect_issues("repo", 1, session, state="all")

        assert count == 1
        session.commit.assert_awaited_once()

    async def test_collect_issues_propagates_non_github_exception_fetching_pr_details(
        self, mocker
    ) -> None:
        collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")
        gh_issue = self._make_issue(is_pr=True)
        repo = MagicMock()
        repo.get_issues.return_value = [gh_issue]
        repo.get_pull.side_effect = RuntimeError("boom")
        collector.gh = MagicMock()
        collector.gh.get_repo.return_value = repo
        session = self._make_session()

        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=self._fake_insert,
        )

        with pytest.raises(RuntimeError, match="boom"):
            await collector.collect_issues("repo", 1, session, state="all")


class TestCollectIssuesGraphQLOpenPath:
    """Tests for GraphQL-backed open issue/PR collection."""

    class _RecordingInsertStatement:
        def __init__(self) -> None:
            self.excluded = MagicMock()
            self.values_kwargs: dict | None = None

        def values(self, **kwargs):
            self.values_kwargs = kwargs
            return self

        def on_conflict_do_update(self, **_kwargs):
            return self

    @staticmethod
    def _make_existing_result(
        last_fetched: datetime | None = None,
        closed_at: datetime | None = None,
    ) -> MagicMock:
        result = MagicMock()
        result.scalar_one_or_none.return_value = last_fetched
        result.one_or_none.return_value = (
            (last_fetched, closed_at)
            if last_fetched is not None or closed_at is not None
            else None
        )
        return result

    @staticmethod
    def _make_issue_node() -> dict:
        return {
            "number": 101,
            "title": "Issue 101",
            "body": "Issue body",
            "state": "OPEN",
            "createdAt": "2025-01-01T00:00:00Z",
            "updatedAt": "2025-01-02T00:00:00Z",
            "closedAt": None,
            "url": "https://github.com/canonical/repo/issues/101",
            "author": {"login": "octocat"},
            "labels": {"nodes": [{"name": "bug"}, {"name": "priority-high"}]},
            "comments": {
                "nodes": [
                    {
                        "author": {"login": "reviewer"},
                        "body": "x" * 1200,
                        "createdAt": "2025-01-03T00:00:00Z",
                    }
                ]
            },
            "timelineItems": {"nodes": []},
        }

    @staticmethod
    def _make_pr_node() -> dict:
        return {
            "number": 202,
            "title": "PR 202",
            "body": "PR body",
            "state": "OPEN",
            "createdAt": "2025-01-04T00:00:00Z",
            "updatedAt": "2025-01-05T00:00:00Z",
            "closedAt": None,
            "mergedAt": None,
            "url": "https://github.com/canonical/repo/pull/202",
            "author": {"login": "maintainer"},
            "labels": {"nodes": [{"name": "enhancement"}]},
            "additions": 10,
            "deletions": 3,
            "changedFiles": 2,
            "comments": {
                "nodes": [
                    {
                        "author": {"login": "commenter"},
                        "body": "looks good",
                        "createdAt": "2025-01-06T00:00:00Z",
                    }
                ]
            },
            "reviews": {
                "nodes": [{"author": {"login": "reviewer1"}, "state": "APPROVED"}]
            },
            "reviewThreads": {"nodes": [{"isResolved": False}, {"isResolved": True}]},
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
                                                    "name": "lint",
                                                    "conclusion": "SUCCESS",
                                                    "status": "COMPLETED",
                                                },
                                                {
                                                    "name": "tests",
                                                    "conclusion": "FAILURE",
                                                    "status": "COMPLETED",
                                                },
                                                {
                                                    "name": "publish",
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
            },
        }

    @staticmethod
    def _make_session_with_no_existing_issue(fetch_count: int) -> AsyncMock:
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[
                result
                for _ in range(fetch_count)
                for result in (
                    TestCollectIssuesGraphQLOpenPath._make_existing_result(),
                    None,
                )
            ]
        )
        session.add = MagicMock()
        session.commit = AsyncMock()
        return session

    async def test_collect_issues_passes_since_to_graphql_open_items(
        self, mocker
    ) -> None:
        collector = GitHubCollector(
            token=_TEST_TOKEN,
            org="canonical",
            maintainers=["maintainer"],
        )
        requester = MagicMock()
        collector.gh = MagicMock()
        collector.gh.requester = requester
        collector.wait_for_rate_limit = MagicMock()

        issue_nodes = [self._make_issue_node()]
        pr_nodes = [self._make_pr_node()]
        paginated_issues = mocker.patch(
            "craft_dashboard.collectors.github.paginated_issues",
            return_value=iter(issue_nodes),
        )
        paginated_pull_requests = mocker.patch(
            "craft_dashboard.collectors.github.paginated_pull_requests",
            return_value=iter(pr_nodes),
        )

        statements: list[
            TestCollectIssuesGraphQLOpenPath._RecordingInsertStatement
        ] = []

        def fake_insert(_table):
            stmt = TestCollectIssuesGraphQLOpenPath._RecordingInsertStatement()
            statements.append(stmt)
            return stmt

        session = self._make_session_with_no_existing_issue(fetch_count=2)
        since = datetime(2025, 1, 9, tzinfo=UTC)

        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=fake_insert,
        )

        count = await collector.collect_issues(
            "repo", 1, session, state="open", since=since
        )

        assert count == 2
        collector.wait_for_rate_limit.assert_called_once_with(resource="graphql")
        collector.gh.get_repo.assert_not_called()
        paginated_issues.assert_called_once_with(
            requester, "canonical", "repo", since=since
        )
        paginated_pull_requests.assert_called_once_with(
            requester, "canonical", "repo", since=since
        )
        session.commit.assert_awaited_once()

        issue_values = statements[0].values_kwargs
        assert issue_values is not None
        assert issue_values["issue_type"] == "issue"
        assert issue_values["state"] == "open"
        assert issue_values["labels"] == ["bug", "priority-high"]
        assert issue_values["author"] == "octocat"
        assert issue_values["author_is_maintainer"] is False
        assert issue_values["comments"] == [
            {
                "author": "reviewer",
                "body": "x" * 1000,
                "created_at": "2025-01-03T00:00:00Z",
                "type": "comment",
            }
        ]
        assert issue_values["metadata_"] == {}

        pr_values = statements[1].values_kwargs
        assert pr_values is not None
        assert pr_values["issue_type"] == "pull_request"
        assert pr_values["state"] == "open"
        assert pr_values["labels"] == ["enhancement"]
        assert pr_values["author"] == "maintainer"
        assert pr_values["author_is_maintainer"] is True
        assert pr_values["comments"] == [
            {
                "author": "commenter",
                "body": "looks good",
                "created_at": "2025-01-06T00:00:00Z",
                "type": "comment",
            }
        ]
        assert pr_values["metadata_"] == {
            "review_status": "approved",
            "review_count": 1,
            "unresolved_review_comments": 1,
            "ci_passing": ["lint"],
            "ci_failing": ["tests"],
            "ci_pending": ["publish"],
            "diff_additions": 10,
            "diff_deletions": 3,
            "diff_files_changed": 2,
        }

    async def test_collect_issues_skips_unchanged_graphql_open_items(
        self, mocker
    ) -> None:
        collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")
        collector.gh = MagicMock()
        collector.gh.requester = MagicMock()
        collector.wait_for_rate_limit = MagicMock()

        mocker.patch(
            "craft_dashboard.collectors.github.paginated_issues",
            return_value=iter([self._make_issue_node()]),
        )
        mocker.patch(
            "craft_dashboard.collectors.github.paginated_pull_requests",
            return_value=iter([]),
        )

        insert = mocker.patch("sqlalchemy.dialects.postgresql.insert")
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[self._make_existing_result(datetime(2025, 1, 3, tzinfo=UTC))]
        )
        session.add = MagicMock()
        session.commit = AsyncMock()

        count = await collector.collect_issues("repo", 1, session, state="open")

        assert count == 0
        insert.assert_not_called()
        collector.gh.get_repo.assert_not_called()
        session.commit.assert_awaited_once()

    async def test_collect_issues_interleaves_graphql_items_before_limit(
        self, mocker
    ) -> None:
        collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")
        collector.gh = MagicMock()
        collector.gh.requester = MagicMock()
        collector.wait_for_rate_limit = MagicMock()

        issue_nodes = [
            self._make_issue_node() | {"number": 101 + offset} for offset in range(3)
        ]
        pr_nodes = [self._make_pr_node()]
        mocker.patch(
            "craft_dashboard.collectors.github.paginated_issues",
            return_value=iter(issue_nodes),
        )
        mocker.patch(
            "craft_dashboard.collectors.github.paginated_pull_requests",
            return_value=iter(pr_nodes),
        )

        statements: list[
            TestCollectIssuesGraphQLOpenPath._RecordingInsertStatement
        ] = []

        def fake_insert(_table):
            stmt = TestCollectIssuesGraphQLOpenPath._RecordingInsertStatement()
            statements.append(stmt)
            return stmt

        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=fake_insert,
        )
        session = self._make_session_with_no_existing_issue(fetch_count=2)

        count = await collector.collect_issues(
            "repo", 1, session, state="open", limit=2
        )

        assert count == 2
        assert [stmt.values_kwargs["issue_type"] for stmt in statements] == [
            "issue",
            "pull_request",
        ]
        collector.gh.get_repo.assert_not_called()

    async def test_collect_issues_handles_graphql_deleted_user_author(
        self, mocker
    ) -> None:
        collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")
        collector.gh = MagicMock()
        collector.gh.requester = MagicMock()
        collector.wait_for_rate_limit = MagicMock()

        mocker.patch(
            "craft_dashboard.collectors.github.paginated_issues",
            return_value=iter([self._make_issue_node() | {"author": None}]),
        )
        mocker.patch(
            "craft_dashboard.collectors.github.paginated_pull_requests",
            return_value=iter([]),
        )

        statements: list[
            TestCollectIssuesGraphQLOpenPath._RecordingInsertStatement
        ] = []

        def fake_insert(_table):
            stmt = TestCollectIssuesGraphQLOpenPath._RecordingInsertStatement()
            statements.append(stmt)
            return stmt

        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=fake_insert,
        )
        session = self._make_session_with_no_existing_issue(fetch_count=1)

        count = await collector.collect_issues("repo", 1, session, state="open")

        assert count == 1
        assert statements[0].values_kwargs is not None
        assert statements[0].values_kwargs["author"] is None
        assert statements[0].values_kwargs["author_is_maintainer"] is False
        assert statements[0].values_kwargs["author_is_bot"] is False

    async def test_collect_issues_handles_graphql_partial_error_null_nested_fields(
        self, mocker
    ) -> None:
        """A partial GraphQL error can null out `labels`/`comments`/`timelineItems`.

        Regression test: previously ``node["labels"]["nodes"]`` (etc.) crashed
        with "'NoneType' object is not iterable" whenever GitHub returned a
        (HTTP 200) partial-error response with one of these nested fields set
        to null — e.g. `RESOURCE_LIMITS_EXCEEDED` on a heavily-nested query
        for a large repo. These should degrade to "no labels/comments/refs"
        instead of aborting collection for the whole batch.
        """
        collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")
        collector.gh = MagicMock()
        collector.gh.requester = MagicMock()
        collector.wait_for_rate_limit = MagicMock()

        node = self._make_issue_node() | {
            "labels": {"nodes": None},
            "comments": {"nodes": None},
            "timelineItems": {"nodes": None},
        }
        mocker.patch(
            "craft_dashboard.collectors.github.paginated_issues",
            return_value=iter([node]),
        )
        mocker.patch(
            "craft_dashboard.collectors.github.paginated_pull_requests",
            return_value=iter([]),
        )

        statements: list[
            TestCollectIssuesGraphQLOpenPath._RecordingInsertStatement
        ] = []

        def fake_insert(_table):
            stmt = TestCollectIssuesGraphQLOpenPath._RecordingInsertStatement()
            statements.append(stmt)
            return stmt

        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=fake_insert,
        )
        session = self._make_session_with_no_existing_issue(fetch_count=1)

        count = await collector.collect_issues("repo", 1, session, state="open")

        assert count == 1
        assert statements[0].values_kwargs is not None
        assert statements[0].values_kwargs["labels"] == []
        assert statements[0].values_kwargs["comments"] == []


class TestFetchPRDetails:
    """Tests for _fetch_pr_details."""

    def test_approved_pr(self) -> None:
        """Returns review_status='approved' when latest unique reviewer approved."""
        mock_review = MagicMock()
        mock_review.user.login = "sergio-cazzolato"
        mock_review.state = "APPROVED"

        last_commit = MagicMock()
        last_commit.get_check_runs.return_value = []

        mock_pr = MagicMock()
        mock_pr.get_reviews.return_value = [mock_review]
        mock_pr.get_review_comments.return_value = []
        mock_pr.get_commits.return_value = [last_commit]
        mock_pr.additions = 50
        mock_pr.deletions = 20
        mock_pr.changed_files = 3

        result = _fetch_pr_details(mock_pr)

        assert result["review_status"] == "approved"
        assert result["review_count"] == 1
        assert result["diff_additions"] == 50
        assert result["diff_deletions"] == 20
        assert result["diff_files_changed"] == 3

    def test_changes_requested(self) -> None:
        """Returns review_status='changes_requested' when any reviewer requested changes."""
        reviews = []
        for state in ["APPROVED", "CHANGES_REQUESTED"]:
            r = MagicMock()
            r.user.login = (
                "sergio-cazzolato" if state == "APPROVED" else "craft-contributor"
            )
            r.state = state
            reviews.append(r)

        mock_pr = MagicMock()
        mock_pr.get_reviews.return_value = reviews
        mock_pr.get_review_comments.return_value = []
        mock_pr.get_commits.return_value = []
        mock_pr.additions = 10
        mock_pr.deletions = 5
        mock_pr.changed_files = 2

        result = _fetch_pr_details(mock_pr)

        assert result["review_status"] == "changes_requested"

    def test_ci_checks_classified(self) -> None:
        """CI checks are split into passing, failing, pending lists."""
        passing_check = MagicMock()
        passing_check.name = "lint"
        passing_check.conclusion = "success"

        failing_check = MagicMock()
        failing_check.name = "integration-tests"
        failing_check.conclusion = "failure"

        pending_check = MagicMock()
        pending_check.name = "build"
        pending_check.conclusion = None  # still running

        last_commit = MagicMock()
        last_commit.get_check_runs.return_value = [
            passing_check,
            failing_check,
            pending_check,
        ]

        commits_list = [last_commit]

        mock_pr = MagicMock()
        mock_pr.get_reviews.return_value = []
        mock_pr.get_review_comments.return_value = []
        mock_pr.get_commits.return_value = commits_list
        mock_pr.additions = 0
        mock_pr.deletions = 0
        mock_pr.changed_files = 0

        result = _fetch_pr_details(mock_pr)

        assert "lint" in result["ci_passing"]
        assert "integration-tests" in result["ci_failing"]
        assert "build" in result["ci_pending"]

    def test_unresolved_review_comments_counted(self) -> None:
        """Counts review comments that are not resolved."""
        resolved = MagicMock()
        resolved.position = None  # resolved comments have position=None

        unresolved = MagicMock()
        unresolved.position = 5  # unresolved comments have a line position

        mock_pr = MagicMock()
        mock_pr.get_reviews.return_value = []
        mock_pr.get_review_comments.return_value = [resolved, unresolved]
        mock_pr.get_commits.return_value = []
        mock_pr.additions = 0
        mock_pr.deletions = 0
        mock_pr.changed_files = 0

        result = _fetch_pr_details(mock_pr)

        assert result["unresolved_review_comments"] == 1


class TestCollectIssuesActivityRecording:
    """DB-backed tests for IssueActivity rows created during issue collection."""

    @staticmethod
    def _make_graphql_issue_node(
        *,
        number: int = 101,
        title: str = "Issue 101",
        updated_at: str = "2025-01-02T00:00:00Z",
        state: str = "OPEN",
        closed_at: str | None = None,
    ) -> dict:
        return {
            "number": number,
            "title": title,
            "body": "Issue body",
            "state": state,
            "createdAt": "2025-01-01T00:00:00Z",
            "updatedAt": updated_at,
            "closedAt": closed_at,
            "url": f"https://github.com/canonical/repo/issues/{number}",
            "author": {"login": "octocat"},
            "labels": {"nodes": [{"name": "bug"}]},
            "comments": {"nodes": []},
            "timelineItems": {"nodes": []},
        }

    @staticmethod
    def _make_rest_issue(
        *,
        number: int = 101,
        title: str = "Issue 101",
        updated_at: datetime = datetime(2025, 1, 4, tzinfo=UTC),
        closed_at: datetime | None = None,
    ) -> MagicMock:
        gh_issue = MagicMock()
        gh_issue.number = number
        gh_issue.title = title
        gh_issue.body = "Issue body"
        gh_issue.state = "closed" if closed_at is not None else "open"
        gh_issue.created_at = datetime(2025, 1, 1, tzinfo=UTC)
        gh_issue.updated_at = updated_at
        gh_issue.closed_at = closed_at
        gh_issue.html_url = f"https://github.com/canonical/repo/issues/{number}"
        gh_issue.user = MagicMock(login="octocat")
        gh_issue.labels = []
        gh_issue.pull_request = None
        gh_issue.get_comments.return_value = []
        return gh_issue

    @staticmethod
    async def _activity_rows(
        session, project_id: int, issue_number: int
    ) -> list[IssueActivity]:
        result = await session.execute(
            sa.select(IssueActivity)
            .where(
                IssueActivity.project_id == project_id,
                IssueActivity.issue_number == issue_number,
            )
            .order_by(IssueActivity.id)
        )
        return list(result.scalars())

    @staticmethod
    def _utc(dt: datetime) -> datetime:
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_collect_issues_records_created_activity_for_new_issue(
        self, mocker, test_db_session
    ) -> None:
        project = make_project()
        test_db_session.add(project)
        await test_db_session.flush()

        collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")
        collector.gh = MagicMock()
        collector.gh.requester = MagicMock()
        collector.wait_for_rate_limit = MagicMock()

        mocker.patch(
            "craft_dashboard.collectors.github.paginated_issues",
            return_value=iter([self._make_graphql_issue_node()]),
        )
        mocker.patch(
            "craft_dashboard.collectors.github.paginated_pull_requests",
            return_value=iter([]),
        )

        with patch("sqlalchemy.dialects.postgresql.insert", side_effect=sqlite_insert):
            count = await collector.collect_issues(
                "repo", project.id, test_db_session, state="open"
            )

        activity_rows = await self._activity_rows(test_db_session, project.id, 101)

        assert count == 1
        assert len(activity_rows) == 1
        assert activity_rows[0].change_type == "created"
        assert activity_rows[0].title == "Issue 101"
        assert self._utc(activity_rows[0].occurred_at) == datetime(
            2025, 1, 2, tzinfo=UTC
        )

    @pytest.mark.asyncio
    async def test_collect_issues_records_updated_activity_for_changed_issue(
        self, mocker, test_db_session
    ) -> None:
        project = make_project()
        test_db_session.add(project)
        await test_db_session.flush()
        existing_issue = make_issue(
            project_id=project.id,
            external_id="101",
            updated_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        existing_issue.last_fetched_at = datetime(2025, 1, 1, tzinfo=UTC)
        test_db_session.add(existing_issue)
        await test_db_session.commit()

        collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")
        collector.gh = MagicMock()
        collector.gh.requester = MagicMock()
        collector.wait_for_rate_limit = MagicMock()

        mocker.patch(
            "craft_dashboard.collectors.github.paginated_issues",
            return_value=iter(
                [
                    self._make_graphql_issue_node(
                        updated_at="2025-01-03T00:00:00Z",
                        title="Updated issue title",
                    )
                ]
            ),
        )
        mocker.patch(
            "craft_dashboard.collectors.github.paginated_pull_requests",
            return_value=iter([]),
        )

        with patch("sqlalchemy.dialects.postgresql.insert", side_effect=sqlite_insert):
            count = await collector.collect_issues(
                "repo", project.id, test_db_session, state="open"
            )

        activity_rows = await self._activity_rows(test_db_session, project.id, 101)

        assert count == 1
        assert len(activity_rows) == 1
        assert activity_rows[0].change_type == "updated"
        assert activity_rows[0].title == "Updated issue title"
        assert self._utc(activity_rows[0].occurred_at) == datetime(
            2025, 1, 3, tzinfo=UTC
        )

    @pytest.mark.asyncio
    async def test_collect_issues_records_closed_activity_for_open_to_closed_issue(
        self, mocker, test_db_session
    ) -> None:
        project = make_project()
        test_db_session.add(project)
        await test_db_session.flush()
        existing_issue = make_issue(
            project_id=project.id,
            external_id="101",
            state="open",
            closed_at=None,
            updated_at=datetime(2025, 1, 2, tzinfo=UTC),
        )
        existing_issue.last_fetched_at = datetime(2025, 1, 2, tzinfo=UTC)
        test_db_session.add(existing_issue)
        await test_db_session.commit()

        collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")
        repo = MagicMock()
        repo.get_issues.return_value = [
            self._make_rest_issue(closed_at=datetime(2025, 1, 4, tzinfo=UTC))
        ]
        collector.gh = MagicMock()
        collector.gh.get_repo.return_value = repo

        mocker.patch(
            "craft_dashboard.collectors.github._fetch_closing_references",
            return_value=[],
        )

        with patch("sqlalchemy.dialects.postgresql.insert", side_effect=sqlite_insert):
            count = await collector.collect_issues(
                "repo",
                project.id,
                test_db_session,
                state="all",
                since=datetime(2025, 1, 1, tzinfo=UTC),
            )

        activity_rows = await self._activity_rows(test_db_session, project.id, 101)

        assert count == 1
        assert len(activity_rows) == 1
        assert activity_rows[0].change_type == "closed"
        assert activity_rows[0].title == "Issue 101"
        assert self._utc(activity_rows[0].occurred_at) == datetime(
            2025, 1, 4, tzinfo=UTC
        )

    @pytest.mark.asyncio
    async def test_collect_issues_skips_activity_for_unchanged_issue(
        self, mocker, test_db_session
    ) -> None:
        project = make_project()
        test_db_session.add(project)
        await test_db_session.flush()
        existing_issue = make_issue(
            project_id=project.id,
            external_id="101",
            updated_at=datetime(2025, 1, 4, tzinfo=UTC),
        )
        existing_issue.last_fetched_at = datetime(2025, 1, 4, tzinfo=UTC)
        test_db_session.add(existing_issue)
        await test_db_session.commit()

        collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")
        collector.gh = MagicMock()
        collector.gh.requester = MagicMock()
        collector.wait_for_rate_limit = MagicMock()

        mocker.patch(
            "craft_dashboard.collectors.github.paginated_issues",
            return_value=iter(
                [self._make_graphql_issue_node(updated_at="2025-01-03T00:00:00Z")]
            ),
        )
        mocker.patch(
            "craft_dashboard.collectors.github.paginated_pull_requests",
            return_value=iter([]),
        )

        count = await collector.collect_issues(
            "repo", project.id, test_db_session, state="open"
        )

        activity_rows = await self._activity_rows(test_db_session, project.id, 101)
        issue_result = await test_db_session.execute(
            sa.select(Issue).where(
                Issue.project_id == project.id,
                Issue.external_id == "101",
                Issue.source == "github",
            )
        )
        stored_issue = issue_result.scalar_one()

        assert count == 0
        assert activity_rows == []
        assert stored_issue.last_fetched_at == datetime(2025, 1, 4, tzinfo=UTC)


class TestCollectReleasesGraphQLPath:
    """Tests for GraphQL-backed release and hotfix branch enumeration."""

    class _RecordingInsertStatement:
        def __init__(self) -> None:
            self.excluded = MagicMock()
            self.values_kwargs: dict | None = None

        def values(self, **kwargs):
            self.values_kwargs = kwargs
            return self

        def on_conflict_do_update(self, **_kwargs):
            return self

    @staticmethod
    def _make_metadata_result(metadata: dict | None = None) -> MagicMock:
        result = MagicMock()
        result.scalar_one_or_none.return_value = metadata
        return result

    async def test_collect_releases_uses_graphql_for_release_and_branch_listing(
        self, mocker
    ) -> None:
        collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")
        requester = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_releases = MagicMock()
        mock_repo.get_branches = MagicMock()

        def compare_side_effect(base: str, head: str) -> MagicMock:
            comparisons = {
                ("4.4.1", "main"): MagicMock(ahead_by=0, behind_by=0),
                ("4.3.2", "hotfix/4.3"): MagicMock(ahead_by=3, behind_by=0),
                ("4.3.2", "main"): MagicMock(ahead_by=0, behind_by=0),
            }
            return comparisons[(base, head)]

        mock_repo.compare.side_effect = compare_side_effect

        collector.gh = MagicMock()
        collector.gh.requester = requester
        collector.gh.get_repo.return_value = mock_repo
        collector.wait_for_rate_limit = MagicMock()

        release_nodes = [
            {
                "tagName": "4.4.1",
                "isPrerelease": False,
                "isDraft": False,
                "createdAt": "2025-04-01T00:00:00Z",
                "publishedAt": "2025-04-02T00:00:00Z",
            },
            {
                "tagName": "4.4.1-rc1",
                "isPrerelease": True,
                "isDraft": False,
                "createdAt": "2025-03-20T00:00:00Z",
                "publishedAt": "2025-03-20T00:00:00Z",
            },
            {
                "tagName": "4.3.2",
                "isPrerelease": False,
                "isDraft": False,
                "createdAt": "2025-03-01T00:00:00Z",
                "publishedAt": None,
            },
            {
                "tagName": "4.3.1",
                "isPrerelease": False,
                "isDraft": False,
                "createdAt": "2025-02-01T00:00:00Z",
                "publishedAt": "2025-02-02T00:00:00Z",
            },
            {
                "tagName": "4.2.9",
                "isPrerelease": False,
                "isDraft": True,
                "createdAt": "2025-01-01T00:00:00Z",
                "publishedAt": "2025-01-02T00:00:00Z",
            },
        ]
        branch_names = ["hotfix/4.3", "hotfix/foo-bar"]
        paginated = mocker.patch(
            "craft_dashboard.collectors.github.paginated_releases_and_branches",
            return_value=(release_nodes, branch_names),
        )

        statements: list[TestCollectReleasesGraphQLPath._RecordingInsertStatement] = []

        def fake_insert(_table):
            stmt = TestCollectReleasesGraphQLPath._RecordingInsertStatement()
            statements.append(stmt)
            return stmt

        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=fake_insert,
        )

        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[
                None,
                self._make_metadata_result(),
                None,
                None,
                self._make_metadata_result(),
                None,
            ]
        )
        session.commit = AsyncMock()

        count = await collector.collect_releases("repo", 7, session)

        assert count == 2
        collector.wait_for_rate_limit.assert_called_once_with(resource="graphql")
        paginated.assert_called_once_with(
            requester, "canonical", "repo", known_since=None
        )
        collector.gh.get_repo.assert_called_once_with("canonical/repo")
        mock_repo.get_releases.assert_not_called()
        mock_repo.get_branches.assert_not_called()
        assert mock_repo.compare.call_args_list == [
            (("4.4.1", "main"),),
            (("4.3.2", "hotfix/4.3"),),
            (("4.3.2", "main"),),
        ]
        session.commit.assert_awaited_once()

        assert len(statements) == 2

        main_values = statements[0].values_kwargs
        assert main_values is not None
        assert main_values["project_id"] == 7
        assert main_values["version"] == "4.4.1"
        assert main_values["branch"] == "main"
        assert main_values["released_at"] == datetime(2025, 4, 2, tzinfo=UTC)
        assert main_values["is_hotfix"] is False
        assert main_values["metadata_"] == {"prerelease": False, "draft": False}

        hotfix_values = statements[1].values_kwargs
        assert hotfix_values is not None
        assert hotfix_values["project_id"] == 7
        assert hotfix_values["version"] == "4.3.2"
        assert hotfix_values["branch"] == "hotfix/4.3"
        assert hotfix_values["released_at"] == datetime(2025, 3, 1, tzinfo=UTC)
        assert hotfix_values["is_hotfix"] is True
        assert hotfix_values["metadata_"] == {"prerelease": False, "draft": False}
