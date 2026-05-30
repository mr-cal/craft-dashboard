"""Tests for issue query helpers."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.routes.issues import (
    _apply_author_role_filter,
    _compute_age_days,
    _query_issues,
)
from sqlalchemy import select
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"


class FrozenDateTime(datetime):
    """Frozen datetime for deterministic age calculations."""

    frozen_now = datetime(2025, 1, 31, 12, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        """Return a fixed current time."""
        if tz is None:
            return cls.frozen_now.replace(tzinfo=None)
        return cls.frozen_now.astimezone(tz)


async def _seed_projects_and_issues(session) -> None:
    snapcraft = Project(
        name="snapcraft", category="app", github_org="canonical", display_order=1
    )
    charmcraft = Project(
        name="charmcraft", category="app", github_org="canonical", display_order=2
    )
    session.add_all([snapcraft, charmcraft])
    await session.flush()

    now = datetime(2025, 1, 31, 12, 0, tzinfo=UTC)
    session.add_all(
        [
            Issue(
                project_id=snapcraft.id,
                source="github",
                external_id="1",
                issue_type="issue",
                title="Alpha bug",
                body="body",
                state="open",
                author="alice",
                author_is_maintainer=True,
                author_is_bot=False,
                labels=[],
                created_at=now - timedelta(days=10),
                updated_at=now - timedelta(days=1),
                closed_at=None,
                url="https://example.test/snapcraft/1",
                metadata_={},
                comments=[],
                last_fetched_at=now,
            ),
            Issue(
                project_id=snapcraft.id,
                source="github",
                external_id="2",
                issue_type="issue",
                title="Beta bug",
                body="body",
                state="open",
                author="bob",
                author_is_maintainer=False,
                author_is_bot=False,
                labels=[],
                created_at=now - timedelta(days=5),
                updated_at=now - timedelta(days=2),
                closed_at=None,
                url="https://example.test/snapcraft/2",
                metadata_={},
                comments=[],
                last_fetched_at=now,
            ),
            Issue(
                project_id=snapcraft.id,
                source="github",
                external_id="3",
                issue_type="pull_request",
                title="Gamma PR",
                body="body",
                state="open",
                author="carol",
                author_is_maintainer=False,
                author_is_bot=False,
                labels=[],
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(hours=12),
                closed_at=None,
                url="https://example.test/snapcraft/3",
                metadata_={},
                comments=[],
                last_fetched_at=now,
            ),
            Issue(
                project_id=charmcraft.id,
                source="github",
                external_id="4",
                issue_type="issue",
                title="Delta bug",
                body="body",
                state="open",
                author="dave",
                author_is_maintainer=False,
                author_is_bot=False,
                labels=[],
                created_at=now - timedelta(days=20),
                updated_at=now - timedelta(days=3),
                closed_at=None,
                url="https://example.test/charmcraft/4",
                metadata_={},
                comments=[],
                last_fetched_at=now,
            ),
        ]
    )
    await session.commit()


async def _seed_author_role_issues(session) -> None:
    project = Project(
        name="snapcraft", category="app", github_org="canonical", display_order=1
    )
    session.add(project)
    await session.flush()

    now = datetime(2025, 1, 31, 12, 0, tzinfo=UTC)
    session.add_all(
        [
            Issue(
                project_id=project.id,
                source="github",
                external_id="10",
                issue_type="issue",
                title="Maintainer issue",
                body="body",
                state="open",
                author="alice",
                author_is_maintainer=True,
                author_is_bot=False,
                labels=[],
                created_at=now - timedelta(days=3),
                updated_at=now - timedelta(days=1),
                closed_at=None,
                url="https://example.test/issues/10",
                metadata_={},
                comments=[],
                last_fetched_at=now,
            ),
            Issue(
                project_id=project.id,
                source="github",
                external_id="11",
                issue_type="issue",
                title="Contributor issue",
                body="body",
                state="open",
                author="bob",
                author_is_maintainer=False,
                author_is_bot=False,
                labels=[],
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=1),
                closed_at=None,
                url="https://example.test/issues/11",
                metadata_={},
                comments=[],
                last_fetched_at=now,
            ),
            Issue(
                project_id=project.id,
                source="github",
                external_id="12",
                issue_type="issue",
                title="Bot issue",
                body="body",
                state="open",
                author="renovate[bot]",
                author_is_maintainer=False,
                author_is_bot=True,
                labels=[],
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(hours=12),
                closed_at=None,
                url="https://example.test/issues/12",
                metadata_={},
                comments=[],
                last_fetched_at=now,
            ),
        ]
    )
    await session.commit()


async def _seed_author_role_column_issues(session) -> None:
    project = Project(
        name="snapcraft", category="app", github_org="canonical", display_order=1
    )
    session.add(project)
    await session.flush()

    now = datetime(2025, 1, 31, 12, 0, tzinfo=UTC)
    session.add_all(
        [
            Issue(
                project_id=project.id,
                source="github",
                external_id="13",
                issue_type="issue",
                title="Maintainer issue",
                body="body",
                state="open",
                author="alice",
                author_is_maintainer=True,
                author_is_bot=False,
                labels=[],
                created_at=now - timedelta(days=3),
                updated_at=now - timedelta(days=1),
                closed_at=None,
                url="https://example.test/issues/13",
                metadata_={},
                comments=[],
                last_fetched_at=now,
            ),
            Issue(
                project_id=project.id,
                source="github",
                external_id="14",
                issue_type="issue",
                title="Contributor issue",
                body="body",
                state="open",
                author="bob",
                author_is_maintainer=False,
                author_is_bot=False,
                labels=[],
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=1),
                closed_at=None,
                url="https://example.test/issues/14",
                metadata_={},
                comments=[],
                last_fetched_at=now,
            ),
            Issue(
                project_id=project.id,
                source="github",
                external_id="15",
                issue_type="issue",
                title="Bot issue",
                body="body",
                state="open",
                author="dependabot",
                author_is_maintainer=False,
                author_is_bot=True,
                labels=[],
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(hours=12),
                closed_at=None,
                url="https://example.test/issues/15",
                metadata_={},
                comments=[],
                last_fetched_at=now,
            ),
        ]
    )
    await session.commit()


async def _seed_sorted_issues(session) -> None:
    project = Project(
        name="snapcraft", category="app", github_org="canonical", display_order=1
    )
    session.add(project)
    await session.flush()

    now = datetime(2025, 1, 31, 12, 0, tzinfo=UTC)
    session.add_all(
        [
            Issue(
                project_id=project.id,
                source="github",
                external_id="20",
                issue_type="issue",
                title="Zulu title",
                body="body",
                state="open",
                author="alice",
                author_is_maintainer=False,
                author_is_bot=False,
                labels=[],
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(hours=6),
                closed_at=None,
                url="https://example.test/issues/20",
                metadata_={},
                comments=[],
                last_fetched_at=now,
            ),
            Issue(
                project_id=project.id,
                source="github",
                external_id="21",
                issue_type="issue",
                title="Alpha title",
                body="body",
                state="open",
                author="bob",
                author_is_maintainer=False,
                author_is_bot=False,
                labels=[],
                created_at=now - timedelta(days=10),
                updated_at=now - timedelta(days=1),
                closed_at=None,
                url="https://example.test/issues/21",
                metadata_={},
                comments=[],
                last_fetched_at=now,
            ),
        ]
    )
    await session.commit()


async def _seed_issue_types(session) -> None:
    project = Project(
        name="snapcraft", category="app", github_org="canonical", display_order=1
    )
    session.add(project)
    await session.flush()

    now = datetime(2025, 1, 31, 12, 0, tzinfo=UTC)
    session.add_all(
        [
            Issue(
                project_id=project.id,
                source="github",
                external_id="30",
                issue_type="issue",
                title="Regular issue",
                body="body",
                state="open",
                author="alice",
                author_is_maintainer=False,
                author_is_bot=False,
                labels=[],
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=1),
                closed_at=None,
                url="https://example.test/issues/30",
                metadata_={},
                comments=[],
                last_fetched_at=now,
            ),
            Issue(
                project_id=project.id,
                source="github",
                external_id="31",
                issue_type="pull_request",
                title="Regular PR",
                body="body",
                state="open",
                author="bob",
                author_is_maintainer=False,
                author_is_bot=False,
                labels=[],
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(hours=18),
                closed_at=None,
                url="https://example.test/issues/31",
                metadata_={},
                comments=[],
                last_fetched_at=now,
            ),
        ]
    )
    await session.commit()


class TestComputeAgeDays:
    def test_none_returns_none(self) -> None:
        assert _compute_age_days(None) is None

    def test_today_returns_zero(self) -> None:
        with patch("craft_dashboard.routes.issues.datetime", FrozenDateTime):
            assert _compute_age_days(FrozenDateTime.frozen_now) == 0

    def test_thirty_days_ago_returns_thirty(self) -> None:
        created_at = FrozenDateTime.frozen_now - timedelta(days=30)

        with patch("craft_dashboard.routes.issues.datetime", FrozenDateTime):
            assert _compute_age_days(created_at) == 30

    def test_naive_datetime_is_handled(self) -> None:
        created_at = datetime(2025, 1, 1, 12, 0)

        with patch("craft_dashboard.routes.issues.datetime", FrozenDateTime):
            assert _compute_age_days(created_at) == 30


class TestQueryIssuesFilters:
    async def test_no_filter_returns_all_open_issues(self, test_db_session) -> None:
        await _seed_projects_and_issues(test_db_session)

        issues, total_pages = await _query_issues(test_db_session, sort_by="title")

        assert len(issues) == 4
        assert total_pages == 1

    async def test_project_filter_returns_matching_project(
        self, test_db_session
    ) -> None:
        await _seed_projects_and_issues(test_db_session)

        issues, _ = await _query_issues(
            test_db_session, project="snapcraft", sort_by="title"
        )

        assert len(issues) == 3
        assert {issue["project_name"] for issue in issues} == {"snapcraft"}

    async def test_multiple_projects_filter_returns_all_matches(
        self, test_db_session
    ) -> None:
        await _seed_projects_and_issues(test_db_session)

        issues, _ = await _query_issues(
            test_db_session, project="snapcraft,charmcraft", sort_by="title"
        )

        assert len(issues) == 4

    async def test_source_filter_returns_github_issues(self, test_db_session) -> None:
        await _seed_projects_and_issues(test_db_session)

        issues, _ = await _query_issues(
            test_db_session, source="github", sort_by="title"
        )

        assert len(issues) == 4
        assert {issue["source"] for issue in issues} == {"github"}

    async def test_pagination_returns_single_page_of_results(
        self, test_db_session
    ) -> None:
        await _seed_projects_and_issues(test_db_session)

        issues, total_pages = await _query_issues(
            test_db_session, page=1, sort_by="title"
        )

        assert len(issues) == 4
        assert total_pages == 1


class TestQueryIssuesAuthorRole:
    async def test_maintainer_filter_returns_only_maintainer(
        self, test_db_session
    ) -> None:
        await _seed_author_role_issues(test_db_session)

        issues, _ = await _query_issues(
            test_db_session, author_role="maintainer", sort_by="title"
        )

        assert len(issues) == 1
        assert [issue["author"] for issue in issues] == ["alice"]

    async def test_contributor_filter_returns_only_contributor(
        self, test_db_session
    ) -> None:
        await _seed_author_role_issues(test_db_session)

        issues, _ = await _query_issues(
            test_db_session, author_role="contributor", sort_by="title"
        )

        assert len(issues) == 1
        assert [issue["author"] for issue in issues] == ["bob"]

    async def test_bot_filter_returns_only_bot(self, test_db_session) -> None:
        await _seed_author_role_issues(test_db_session)

        issues, _ = await _query_issues(
            test_db_session,
            author_role="bot",
            sort_by="title",
        )

        assert len(issues) == 1
        assert [issue["author"] for issue in issues] == ["renovate[bot]"]

    async def test_multiple_author_roles_return_combined_matches(
        self, test_db_session
    ) -> None:
        await _seed_author_role_issues(test_db_session)

        issues, _ = await _query_issues(
            test_db_session,
            author_role="maintainer,contributor",
            sort_by="title",
        )

        assert len(issues) == 2
        assert {issue["author"] for issue in issues} == {"alice", "bob"}


class TestQueryIssuesAuthorRoleColumn:
    async def test_bot_filter_matches_author_is_bot_without_suffix(
        self, test_db_session
    ) -> None:
        await _seed_author_role_column_issues(test_db_session)

        issues, _ = await _query_issues(
            test_db_session,
            author_role="bot",
            sort_by="title",
        )

        assert [issue["author"] for issue in issues] == ["dependabot"]

    async def test_bot_filter_excludes_non_bot_maintainer(
        self, test_db_session
    ) -> None:
        await _seed_author_role_column_issues(test_db_session)

        issues, _ = await _query_issues(
            test_db_session,
            author_role="bot",
            sort_by="title",
        )

        assert "alice" not in {issue["author"] for issue in issues}

    async def test_contributor_filter_matches_non_bot_contributor(
        self, test_db_session
    ) -> None:
        await _seed_author_role_column_issues(test_db_session)

        issues, _ = await _query_issues(
            test_db_session,
            author_role="contributor",
            sort_by="title",
        )

        assert [issue["author"] for issue in issues] == ["bob"]


class TestApplyAuthorRoleFilter:
    async def test_combines_maintainer_and_bot_filters_using_columns(
        self, test_db_session
    ) -> None:
        await _seed_author_role_column_issues(test_db_session)

        query = _apply_author_role_filter(
            select(Issue.author),
            "maintainer,bot",
        ).order_by(Issue.author)

        authors = list((await test_db_session.execute(query)).scalars())

        assert authors == ["alice", "dependabot"]

    async def test_empty_author_role_leaves_query_unfiltered(
        self, test_db_session
    ) -> None:
        await _seed_author_role_column_issues(test_db_session)

        query = _apply_author_role_filter(select(Issue.author), "").order_by(
            Issue.author
        )

        authors = list((await test_db_session.execute(query)).scalars())

        assert authors == ["alice", "bob", "dependabot"]


class TestQueryIssuesSearch:
    async def test_search_by_title(self, test_db_session) -> None:
        """Search filter matches issues by title."""
        await _seed_projects_and_issues(test_db_session)

        issues, _ = await _query_issues(
            test_db_session, search="Alpha", sort_by="title"
        )

        assert len(issues) == 1
        assert issues[0]["title"] == "Alpha bug"

    async def test_search_by_external_id(self, test_db_session) -> None:
        """Search filter matches issues by external_id."""
        await _seed_projects_and_issues(test_db_session)

        issues, _ = await _query_issues(test_db_session, search="1", sort_by="title")

        assert len(issues) == 1
        assert issues[0]["external_id"] == "1"

    async def test_search_no_match(self, test_db_session) -> None:
        """Search filter with no matching term returns empty."""
        await _seed_projects_and_issues(test_db_session)

        issues, _ = await _query_issues(
            test_db_session, search="nonexistent-xyz", sort_by="title"
        )

        assert len(issues) == 0


class TestQueryIssuesPerPage:
    async def test_items_per_page_limits_results(self, test_db_session) -> None:
        """items_per_page parameter limits the number of returned issues."""
        await _seed_projects_and_issues(test_db_session)

        issues, total_pages = await _query_issues(
            test_db_session, items_per_page=2, sort_by="title"
        )

        assert len(issues) == 2
        assert total_pages == 2

    async def test_items_per_page_pagination(self, test_db_session) -> None:
        """Page 2 with items_per_page=2 returns remaining issues."""
        await _seed_projects_and_issues(test_db_session)

        issues, total_pages = await _query_issues(
            test_db_session, items_per_page=2, page=2, sort_by="title"
        )

        assert len(issues) == 2
        assert total_pages == 2


class TestQueryIssuesSortValidation:
    async def test_invalid_sort_field_defaults_to_staleness(
        self, test_db_session
    ) -> None:
        """An invalid sort field falls back to staleness sort."""
        await _seed_projects_and_issues(test_db_session)

        issues, _ = await _query_issues(test_db_session, sort_by="invalid_field")

        assert len(issues) == 4


class TestQueryIssuesSort:
    async def test_title_sort_returns_alphabetical_order(self, test_db_session) -> None:
        await _seed_sorted_issues(test_db_session)

        issues, _ = await _query_issues(test_db_session, sort_by="title")

        assert [issue["title"] for issue in issues] == ["Alpha title", "Zulu title"]

    async def test_reverse_title_sort_returns_reverse_alphabetical_order(
        self, test_db_session
    ) -> None:
        await _seed_sorted_issues(test_db_session)

        issues, _ = await _query_issues(test_db_session, sort_by="-title")

        assert [issue["title"] for issue in issues] == ["Zulu title", "Alpha title"]

    async def test_age_sort_returns_oldest_first(self, test_db_session) -> None:
        await _seed_sorted_issues(test_db_session)

        issues, _ = await _query_issues(test_db_session, sort_by="age")

        assert [issue["title"] for issue in issues] == ["Alpha title", "Zulu title"]


class TestQueryIssuesIssueType:
    async def test_issue_type_issue_filters_to_issues(self, test_db_session) -> None:
        await _seed_issue_types(test_db_session)

        issues, _ = await _query_issues(
            test_db_session, issue_type="issue", sort_by="title"
        )

        assert len(issues) == 1
        assert [issue["issue_type"] for issue in issues] == ["issue"]

    async def test_issue_type_pull_request_filters_to_prs(
        self, test_db_session
    ) -> None:
        await _seed_issue_types(test_db_session)

        issues, _ = await _query_issues(
            test_db_session, issue_type="pull_request", sort_by="title"
        )

        assert len(issues) == 1
        assert [issue["issue_type"] for issue in issues] == ["pull_request"]


async def _seed_issues_with_scores(session) -> None:
    """Seed issues with LLM evaluation scores for testing."""
    project = Project(
        name="snapcraft", category="app", github_org="canonical", display_order=1
    )
    session.add(project)
    await session.flush()

    now = datetime(2025, 1, 31, 12, 0, tzinfo=UTC)

    # Issue with high staleness, low readiness
    issue_stale = Issue(
        project_id=project.id,
        source="github",
        external_id="100",
        issue_type="issue",
        title="Stale issue",
        body="body",
        state="open",
        author="alice",
        author_is_maintainer=False,
        author_is_bot=False,
        labels=[],
        created_at=now - timedelta(days=30),
        updated_at=now - timedelta(days=30),
        closed_at=None,
        url="https://example.test/issues/100",
        metadata_={},
        comments=[],
        last_fetched_at=now,
    )
    session.add(issue_stale)
    await session.flush()

    eval_stale = LLMEvaluation(
        issue_id=issue_stale.id,
        model_name="test-model",
        summary="Very stale issue",
        suggested_action="close",
        suggested_action_reason="No activity",
        scores={
            "staleness": 0.95,
            "duplicateness": 0.1,
            "complexity": 0.3,
            "support_request": 0.2,
            "readiness": 0.2,
        },
        latest=True,
    )
    session.add(eval_stale)

    # Issue with low staleness, high readiness
    issue_ready = Issue(
        project_id=project.id,
        source="github",
        external_id="101",
        issue_type="issue",
        title="Ready issue",
        body="body",
        state="open",
        author="bob",
        author_is_maintainer=False,
        author_is_bot=False,
        labels=[],
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(hours=1),
        closed_at=None,
        url="https://example.test/issues/101",
        metadata_={},
        comments=[],
        last_fetched_at=now,
    )
    session.add(issue_ready)
    await session.flush()

    eval_ready = LLMEvaluation(
        issue_id=issue_ready.id,
        model_name="test-model",
        summary="Ready to work on",
        suggested_action="work",
        suggested_action_reason="Good first issue",
        scores={
            "staleness": 0.1,
            "duplicateness": 0.05,
            "complexity": 0.2,
            "support_request": 0.05,
            "readiness": 0.9,
        },
        latest=True,
    )
    session.add(eval_ready)

    # Issue with high complexity
    issue_complex = Issue(
        project_id=project.id,
        source="github",
        external_id="102",
        issue_type="issue",
        title="Complex issue",
        body="body",
        state="open",
        author="carol",
        author_is_maintainer=False,
        author_is_bot=False,
        labels=[],
        created_at=now - timedelta(days=5),
        updated_at=now - timedelta(days=2),
        closed_at=None,
        url="https://example.test/issues/102",
        metadata_={},
        comments=[],
        last_fetched_at=now,
    )
    session.add(issue_complex)
    await session.flush()

    eval_complex = LLMEvaluation(
        issue_id=issue_complex.id,
        model_name="test-model",
        summary="Complex architecture change",
        suggested_action="investigate",
        suggested_action_reason="Requires deep analysis",
        scores={
            "staleness": 0.4,
            "duplicateness": 0.15,
            "complexity": 0.95,
            "support_request": 0.1,
            "readiness": 0.5,
        },
        latest=True,
    )
    session.add(eval_complex)

    # Issue without scores (no LLM evaluation)
    issue_no_scores = Issue(
        project_id=project.id,
        source="github",
        external_id="103",
        issue_type="issue",
        title="Unscored issue",
        body="body",
        state="open",
        author="dave",
        author_is_maintainer=False,
        author_is_bot=False,
        labels=[],
        created_at=now - timedelta(days=3),
        updated_at=now - timedelta(days=1),
        closed_at=None,
        url="https://example.test/issues/103",
        metadata_={},
        comments=[],
        last_fetched_at=now,
    )
    session.add(issue_no_scores)

    await session.commit()


class TestQueryIssuesLLMScores:
    """Test that _query_issues properly returns all LLM score fields."""

    async def test_query_returns_all_score_fields(self, test_db_session) -> None:
        """_query_issues should return all LLM score fields for issues."""
        await _seed_issues_with_scores(test_db_session)

        issues, _ = await _query_issues(test_db_session, sort_by="staleness")

        # Find the issue with scores
        scored_issue = next(
            (issue for issue in issues if issue["external_id"] == "100"), None
        )
        assert scored_issue is not None

        # Verify all score fields are present
        assert "staleness" in scored_issue
        assert "duplicateness" in scored_issue
        assert "complexity" in scored_issue
        assert "support_request" in scored_issue
        assert "readiness" in scored_issue
        assert "suggested_action" in scored_issue
        assert "suggested_action_reason" in scored_issue

        # Verify score values
        assert scored_issue["staleness"] == 0.95
        assert scored_issue["duplicateness"] == 0.1
        assert scored_issue["complexity"] == 0.3
        assert scored_issue["support_request"] == 0.2
        assert scored_issue["readiness"] == 0.2
        assert scored_issue["suggested_action"] == "close"
        assert scored_issue["suggested_action_reason"] == "No activity"

    async def test_query_handles_missing_scores(self, test_db_session) -> None:
        """_query_issues should handle issues without LLM evaluations."""
        await _seed_issues_with_scores(test_db_session)

        issues, _ = await _query_issues(test_db_session, sort_by="title")

        # Find the issue without scores
        unscored_issue = next(
            (issue for issue in issues if issue["external_id"] == "103"), None
        )
        assert unscored_issue is not None

        # Verify score fields are None
        assert unscored_issue["staleness"] is None
        assert unscored_issue["duplicateness"] is None
        assert unscored_issue["complexity"] is None
        assert unscored_issue["support_request"] is None
        assert unscored_issue["readiness"] is None
        assert unscored_issue["suggested_action"] is None
        assert unscored_issue["suggested_action_reason"] is None

    async def test_sort_by_staleness_score(self, test_db_session) -> None:
        """Sort by staleness should order by staleness score descending."""
        await _seed_issues_with_scores(test_db_session)

        issues, _ = await _query_issues(test_db_session, sort_by="staleness")

        # First issue should be the one with highest staleness
        assert issues[0]["external_id"] == "100"
        assert issues[0]["staleness"] == 0.95

    async def test_sort_by_readiness_score(self, test_db_session) -> None:
        """Sort by readiness should order by readiness score descending."""
        await _seed_issues_with_scores(test_db_session)

        issues, _ = await _query_issues(test_db_session, sort_by="readiness")

        # First issue should be the one with highest readiness
        assert issues[0]["external_id"] == "101"
        assert issues[0]["readiness"] == 0.9

    async def test_sort_by_complexity_score(self, test_db_session) -> None:
        """Sort by complexity should order by complexity score descending."""
        await _seed_issues_with_scores(test_db_session)

        issues, _ = await _query_issues(test_db_session, sort_by="complexity")

        # First issue should be the one with highest complexity
        assert issues[0]["external_id"] == "102"
        assert issues[0]["complexity"] == 0.95

    async def test_sort_by_duplicateness_score(self, test_db_session) -> None:
        """Sort by duplicateness should order by duplicateness score descending."""
        await _seed_issues_with_scores(test_db_session)

        issues, _ = await _query_issues(test_db_session, sort_by="duplicateness")

        # First issue should be the one with highest duplicateness
        assert issues[0]["external_id"] == "102"
        assert issues[0]["duplicateness"] == 0.15

    async def test_sort_by_support_request_score(self, test_db_session) -> None:
        """Sort by support_request should order by support_request score descending."""
        await _seed_issues_with_scores(test_db_session)

        issues, _ = await _query_issues(test_db_session, sort_by="support_request")

        # First issue should be the one with highest support_request
        assert issues[0]["external_id"] == "100"
        assert issues[0]["support_request"] == 0.2

    async def test_reverse_sort_by_readiness_score(self, test_db_session) -> None:
        """Reverse sort by readiness should order by readiness score ascending."""
        await _seed_issues_with_scores(test_db_session)

        issues, _ = await _query_issues(test_db_session, sort_by="-readiness")

        # Last scored issue should be the one with highest readiness
        # (issues with no scores come first with 0)
        scored_issues = [i for i in issues if i["readiness"] is not None]
        assert scored_issues[-1]["external_id"] == "101"
        assert scored_issues[-1]["readiness"] == 0.9


class TestLLMStatusFilter:
    """Tests for the llm_status filter parameter."""

    async def test_no_llm_filter_returns_unevaluated_issues(
        self, test_db_session
    ) -> None:
        """llm_status=no_llm should return only issues without LLM evaluations."""
        await _seed_projects_and_issues(test_db_session)
        await test_db_session.commit()

        # All seeded issues have no LLM evaluations
        issues, _ = await _query_issues(test_db_session, llm_status="no_llm")
        assert len(issues) > 0

        # Now add an evaluation for one issue
        issue_result = await test_db_session.execute(
            select(Issue).where(Issue.external_id == "1")
        )
        issue = issue_result.scalar_one()
        test_db_session.add(
            LLMEvaluation(
                issue_id=issue.id,
                model_name="test",
                summary="A summary",
                suggested_action="keep_open",
                suggested_action_reason="reason",
                scores={"staleness": 0.5},
                latest=True,
            )
        )
        await test_db_session.commit()

        issues_after, _ = await _query_issues(test_db_session, llm_status="no_llm")
        ids = [i["external_id"] for i in issues_after]
        assert "1" not in ids

    async def test_partial_llm_filter_returns_incomplete_evaluations(
        self, test_db_session
    ) -> None:
        """llm_status=partial_llm should return issues with incomplete LLM data."""
        await _seed_projects_and_issues(test_db_session)
        await test_db_session.commit()

        issue_result = await test_db_session.execute(
            select(Issue).where(Issue.external_id == "1")
        )
        issue = issue_result.scalar_one()
        # Add evaluation with missing summary
        test_db_session.add(
            LLMEvaluation(
                issue_id=issue.id,
                model_name="test",
                summary="",
                suggested_action="keep_open",
                suggested_action_reason="reason",
                scores={"staleness": 0.5},
                latest=True,
            )
        )
        await test_db_session.commit()

        issues, _ = await _query_issues(test_db_session, llm_status="partial_llm")
        ids = [i["external_id"] for i in issues]
        assert "1" in ids

    async def test_empty_llm_status_returns_all(self, test_db_session) -> None:
        """Empty llm_status should not filter anything."""
        await _seed_projects_and_issues(test_db_session)
        await test_db_session.commit()

        all_issues, _ = await _query_issues(test_db_session, llm_status="")
        no_filter, _ = await _query_issues(test_db_session)
        assert len(all_issues) == len(no_filter)
