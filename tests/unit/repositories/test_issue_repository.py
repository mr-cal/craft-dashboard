"""Tests for the issue repository."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from craft_dashboard.models.issue import Issue
from craft_dashboard.models.project import Project
from craft_dashboard.models.views import IssueFilters, IssueQueryResult, IssueView
from craft_dashboard.repositories.issue_repository import (
    IssueRepository,
    _apply_author_role_filter,
    _compute_age_days,
)
from sqlalchemy import select
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

from tests.factories import make_evaluation, make_issue, make_project

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


FIXED_NOW = FrozenDateTime.frozen_now


async def _query(session, **kwargs):
    result = await IssueRepository(session).search(IssueFilters(**kwargs))
    return result.issues, result.total_count, result.total_pages


async def _add_project(session, **kwargs) -> Project:
    project = make_project(category="app", **kwargs)
    session.add(project)
    await session.flush()
    return project


async def _seed_projects_and_issues(session) -> None:
    snapcraft = await _add_project(session, name="snapcraft")
    charmcraft = await _add_project(session, name="charmcraft", display_order=2)
    session.add_all(
        [
            make_issue(
                project_id=snapcraft.id,
                external_id="1",
                title="Add support for core24 base",
                body="Support `base: core24` in snapcraft projects targeting Ubuntu 24.04.",
                author="sergio-cazzolato",
                author_is_maintainer=True,
                created_at=FIXED_NOW - timedelta(days=10),
                updated_at=FIXED_NOW - timedelta(days=1),
                url="https://github.com/canonical/snapcraft/issues/1234",
            ),
            make_issue(
                project_id=snapcraft.id,
                external_id="2",
                title="Snap refresh fails when revision pinned",
                body="Refreshing a snapped app fails when `snap refresh --revision` targets a pinned revision.",
                author="craft-contributor",
                created_at=FIXED_NOW - timedelta(days=5),
                updated_at=FIXED_NOW - timedelta(days=2),
                url="https://github.com/canonical/snapcraft/issues/2235",
            ),
            make_issue(
                project_id=snapcraft.id,
                external_id="3",
                issue_type="pull_request",
                title="fix: handle empty manifest gracefully",
                body="Avoid a traceback when snap metadata is rendered from an empty manifest during pack.",
                author="craft-contributor",
                created_at=FIXED_NOW - timedelta(days=1),
                updated_at=FIXED_NOW - timedelta(hours=12),
                url="https://github.com/canonical/snapcraft/pull/2236",
            ),
            make_issue(
                project_id=charmcraft.id,
                external_id="4",
                title="charmcraft deploy times out on large bundles",
                body="Deploying a large bundle stalls while charmcraft waits for the controller response.",
                author="craft-contributor",
                created_at=FIXED_NOW - timedelta(days=20),
                updated_at=FIXED_NOW - timedelta(days=3),
                url="https://github.com/canonical/charmcraft/issues/4324",
            ),
        ]
    )
    await session.commit()


async def _seed_author_role_issues(session) -> None:
    project = await _add_project(session, name="snapcraft")
    session.add_all(
        [
            make_issue(
                project_id=project.id,
                external_id="10",
                title="Add support for core24 base",
                body="Support `base: core24` in snapcraft projects targeting Ubuntu 24.04.",
                author="sergio-cazzolato",
                author_is_maintainer=True,
                created_at=FIXED_NOW - timedelta(days=3),
                updated_at=FIXED_NOW - timedelta(days=1),
                url="https://github.com/canonical/snapcraft/issues/2234",
            ),
            make_issue(
                project_id=project.id,
                external_id="11",
                title="Snap refresh fails when revision pinned",
                body="Refreshing a snapped app fails when `snap refresh --revision` targets a pinned revision.",
                author="craft-contributor",
                created_at=FIXED_NOW - timedelta(days=2),
                updated_at=FIXED_NOW - timedelta(days=1),
                url="https://github.com/canonical/snapcraft/issues/2235",
            ),
            make_issue(
                project_id=project.id,
                external_id="12",
                title="chore: refresh core24 test dependencies",
                body="Automated dependency refresh for the core24 integration test matrix.",
                author="renovate[bot]",
                author_is_bot=True,
                created_at=FIXED_NOW - timedelta(days=1),
                updated_at=FIXED_NOW - timedelta(hours=12),
                url="https://github.com/canonical/snapcraft/pull/2236",
            ),
        ]
    )
    await session.commit()


async def _seed_author_role_column_issues(session) -> None:
    project = await _add_project(session, name="snapcraft")
    session.add_all(
        [
            make_issue(
                project_id=project.id,
                external_id="13",
                title="Add support for core24 base",
                body="Support `base: core24` in snapcraft projects targeting Ubuntu 24.04.",
                author="sergio-cazzolato",
                author_is_maintainer=True,
                created_at=FIXED_NOW - timedelta(days=3),
                updated_at=FIXED_NOW - timedelta(days=1),
                url="https://github.com/canonical/snapcraft/issues/3234",
            ),
            make_issue(
                project_id=project.id,
                external_id="14",
                title="Snap refresh fails when revision pinned",
                body="Refreshing a snapped app fails when `snap refresh --revision` targets a pinned revision.",
                author="craft-contributor",
                created_at=FIXED_NOW - timedelta(days=2),
                updated_at=FIXED_NOW - timedelta(days=1),
                url="https://github.com/canonical/snapcraft/issues/3235",
            ),
            make_issue(
                project_id=project.id,
                external_id="15",
                title="chore: refresh spread dependencies",
                body="Automated dependency refresh for spread and integration jobs.",
                author="renovate[bot]",
                author_is_bot=True,
                created_at=FIXED_NOW - timedelta(days=1),
                updated_at=FIXED_NOW - timedelta(hours=12),
                url="https://github.com/canonical/snapcraft/pull/3236",
            ),
        ]
    )
    await session.commit()


async def _seed_sorted_issues(session) -> None:
    project = await _add_project(session, name="snapcraft")
    session.add_all(
        [
            make_issue(
                project_id=project.id,
                external_id="20",
                title="Snap refresh fails when revision pinned",
                body="Refreshing a snapped app fails when `snap refresh --revision` targets a pinned revision.",
                author="craft-contributor",
                created_at=FIXED_NOW - timedelta(days=1),
                updated_at=FIXED_NOW - timedelta(hours=6),
                url="https://github.com/canonical/snapcraft/issues/4234",
            ),
            make_issue(
                project_id=project.id,
                external_id="21",
                title="Add support for core24 base",
                body="Support `base: core24` in snapcraft projects targeting Ubuntu 24.04.",
                author="sergio-cazzolato",
                created_at=FIXED_NOW - timedelta(days=10),
                updated_at=FIXED_NOW - timedelta(days=1),
                url="https://github.com/canonical/snapcraft/issues/4235",
            ),
        ]
    )
    await session.commit()


async def _seed_issue_types(session) -> None:
    project = await _add_project(session, name="snapcraft")
    session.add_all(
        [
            make_issue(
                project_id=project.id,
                external_id="30",
                title="Snap refresh fails when revision pinned",
                body="Refreshing a snapped app fails when `snap refresh --revision` targets a pinned revision.",
                author="craft-contributor",
                created_at=FIXED_NOW - timedelta(days=2),
                updated_at=FIXED_NOW - timedelta(days=1),
                url="https://github.com/canonical/snapcraft/issues/5234",
            ),
            make_issue(
                project_id=project.id,
                external_id="31",
                issue_type="pull_request",
                title="fix: handle empty manifest gracefully",
                body="Avoid a traceback when snap metadata is rendered from an empty manifest during pack.",
                author="sergio-cazzolato",
                created_at=FIXED_NOW - timedelta(days=1),
                updated_at=FIXED_NOW - timedelta(hours=18),
                url="https://github.com/canonical/snapcraft/pull/5235",
            ),
        ]
    )
    await session.commit()


async def _seed_issues_with_scores(session) -> None:
    project = await _add_project(session, name="snapcraft")

    issue_stale = make_issue(
        project_id=project.id,
        external_id="100",
        title="snapcraft pack fails with LXD backend on Ubuntu 24.04",
        body="When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
        author="craft-contributor",
        created_at=FIXED_NOW - timedelta(days=30),
        updated_at=FIXED_NOW - timedelta(days=30),
        url="https://github.com/canonical/snapcraft/issues/6100",
    )
    session.add(issue_stale)
    await session.flush()
    session.add(
        make_evaluation(
            issue_id=issue_stale.id,
            summary="Stale LXD regression report without recent maintainer follow-up",
            suggested_action="close",
            suggested_action_reason="No activity since the Ubuntu 24.04 migration",
            scores={
                "staleness": 0.95,
                "complexity": 0.3,
                "support_request": 0.2,
                "confidence": 70.0,
            },
        )
    )

    issue_ready = make_issue(
        project_id=project.id,
        external_id="101",
        title="Add support for core24 base",
        body="Please add support for `base: core24` so new snaps can target Ubuntu 24.04.",
        author="sergio-cazzolato",
        created_at=FIXED_NOW - timedelta(days=1),
        updated_at=FIXED_NOW - timedelta(hours=1),
        url="https://github.com/canonical/snapcraft/issues/6101",
    )
    session.add(issue_ready)
    await session.flush()
    session.add(
        make_evaluation(
            issue_id=issue_ready.id,
            summary="Clear enhancement request with maintainers aligned on core24 support",
            suggested_action="work",
            suggested_action_reason="Implementation scope is clear and unblocked",
            scores={
                "staleness": 0.1,
                "complexity": 0.2,
                "support_request": 0.05,
                "confidence": 85.0,
            },
        )
    )

    issue_complex = make_issue(
        project_id=project.id,
        external_id="102",
        title="Refactor manifest parsing for multi-arch builds",
        body="Rework manifest parsing so multi-architecture builds share a consistent metadata pipeline.",
        author="jdoe-canonical",
        created_at=FIXED_NOW - timedelta(days=5),
        updated_at=FIXED_NOW - timedelta(days=2),
        url="https://github.com/canonical/snapcraft/issues/6102",
    )
    session.add(issue_complex)
    await session.flush()
    session.add(
        make_evaluation(
            issue_id=issue_complex.id,
            summary="Large refactor touching manifest parsing and multi-arch build logic",
            suggested_action="investigate",
            suggested_action_reason="Touches multiple build stages and architecture-specific paths",
            scores={
                "staleness": 0.4,
                "complexity": 0.95,
                "support_request": 0.1,
                "confidence": 60.0,
            },
        )
    )

    session.add(
        make_issue(
            project_id=project.id,
            external_id="103",
            title="charmcraft deploy times out on large bundles",
            body="Deploying a large bundle stalls while charmcraft waits for the controller response.",
            author="craft-contributor",
            created_at=FIXED_NOW - timedelta(days=3),
            updated_at=FIXED_NOW - timedelta(days=1),
            url="https://github.com/canonical/charmcraft/issues/6103",
        )
    )

    await session.commit()


class TestComputeAgeDays:
    def test_none_returns_none(self) -> None:
        assert _compute_age_days(None) is None

    def test_today_returns_zero(self) -> None:
        with patch(
            "craft_dashboard.repositories.issue_repository.datetime", FrozenDateTime
        ):
            assert _compute_age_days(FrozenDateTime.frozen_now) == 0

    def test_thirty_days_ago_returns_thirty(self) -> None:
        created_at = FrozenDateTime.frozen_now - timedelta(days=30)

        with patch(
            "craft_dashboard.repositories.issue_repository.datetime", FrozenDateTime
        ):
            assert _compute_age_days(created_at) == 30

    def test_naive_datetime_is_handled(self) -> None:
        created_at = datetime(2025, 1, 1, 12, 0)

        with patch(
            "craft_dashboard.repositories.issue_repository.datetime", FrozenDateTime
        ):
            assert _compute_age_days(created_at) == 30


class _DetailQueryResult:
    def __init__(self, row=None, evaluations=None):
        self._row = row
        self._evaluations = evaluations or []

    def first(self):
        return self._row

    def scalars(self):
        return iter(self._evaluations)


class _DetailRow:
    def __init__(
        self,
        issue,
        *,
        project_name,
        summary,
        suggested_action,
        suggested_action_reason,
        scores,
    ):
        self._issue = issue
        self.project_name = project_name
        self.summary = summary
        self.suggested_action = suggested_action
        self.suggested_action_reason = suggested_action_reason
        self.scores = scores

    def __getitem__(self, index):
        if index == 0:
            return self._issue
        raise IndexError(index)


class TestGetIssueDetail:
    async def test_returns_issue_with_current_evaluation_and_history(self) -> None:
        issue = make_issue(
            project_id=1,
            external_id="321",
            title="Support core24 builds end to end",
            body="Steps to reproduce\n1. Build\n2. Observe failure",
            author="sergio-cazzolato",
            labels=["bug", "core24"],
            created_at=FIXED_NOW - timedelta(days=5),
            updated_at=FIXED_NOW - timedelta(days=1),
            url="https://github.com/canonical/snapcraft/issues/321",
        )
        latest = make_evaluation(
            issue_id=issue.id,
            model_name="gpt-4.1",
            summary="Regression in the core24 build pipeline.",
            suggested_action="needs_review",
            suggested_action_reason="Recent failures need maintainer attention.",
            scores={"staleness": 0.2, "complexity": 0.7},
            evaluated_at=FIXED_NOW - timedelta(hours=1),
            latest=True,
        )
        earlier = make_evaluation(
            issue_id=issue.id,
            model_name="gpt-4o-mini",
            summary="Earlier summary.",
            suggested_action="keep_open",
            suggested_action_reason="Still active.",
            scores={"staleness": 0.1},
            evaluated_at=FIXED_NOW - timedelta(days=1),
            latest=False,
        )
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[
                _DetailQueryResult(
                    _DetailRow(
                        issue,
                        project_name="snapcraft",
                        summary=latest.summary,
                        suggested_action=latest.suggested_action,
                        suggested_action_reason=latest.suggested_action_reason,
                        scores=latest.scores,
                    )
                ),
                _DetailQueryResult(evaluations=[latest, earlier]),
            ]
        )

        detail = await IssueRepository(session).get_issue_detail("snapcraft", "321")

        assert detail is not None
        assert detail["project_name"] == "snapcraft"
        assert detail["title"] == "Support core24 builds end to end"
        assert detail["summary"] == "Regression in the core24 build pipeline."
        assert detail["suggested_action"] == "needs_review"
        assert [entry["model_name"] for entry in detail["evaluation_history"]] == [
            "gpt-4.1",
            "gpt-4o-mini",
        ]

    async def test_returns_none_when_issue_does_not_exist(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_DetailQueryResult())

        detail = await IssueRepository(session).get_issue_detail("snapcraft", "999")

        assert detail is None


class TestQueryIssuesFilters:
    async def test_returns_issue_query_result_with_issue_views(
        self, test_db_session
    ) -> None:
        await _seed_projects_and_issues(test_db_session)

        result = await IssueRepository(test_db_session).search(
            IssueFilters(sort_by="title")
        )

        assert isinstance(result, IssueQueryResult)
        assert result.total_count == 4
        assert result.total_pages == 1
        assert result.page == 1
        assert isinstance(result.issues[0], IssueView)

    async def test_no_filter_returns_all_open_issues(self, test_db_session) -> None:
        await _seed_projects_and_issues(test_db_session)

        issues, _, total_pages = await _query(test_db_session, sort_by="title")

        assert len(issues) == 4
        assert total_pages == 1

    async def test_project_filter_returns_matching_project(
        self, test_db_session
    ) -> None:
        await _seed_projects_and_issues(test_db_session)

        issues, *_ = await _query(test_db_session, project="snapcraft", sort_by="title")

        assert len(issues) == 3
        assert {issue.project_name for issue in issues} == {"snapcraft"}

    async def test_multiple_projects_filter_returns_all_matches(
        self, test_db_session
    ) -> None:
        await _seed_projects_and_issues(test_db_session)

        issues, *_ = await _query(
            test_db_session, project="snapcraft,charmcraft", sort_by="title"
        )

        assert len(issues) == 4

    async def test_source_filter_returns_github_issues(self, test_db_session) -> None:
        await _seed_projects_and_issues(test_db_session)

        issues, *_ = await _query(test_db_session, source="github", sort_by="title")

        assert len(issues) == 4
        assert {issue.source for issue in issues} == {"github"}

    async def test_pagination_returns_single_page_of_results(
        self, test_db_session
    ) -> None:
        await _seed_projects_and_issues(test_db_session)

        issues, _, total_pages = await _query(test_db_session, page=1, sort_by="title")

        assert len(issues) == 4
        assert total_pages == 1


class TestQueryIssuesAuthorRole:
    async def test_maintainer_filter_returns_only_maintainer(
        self, test_db_session
    ) -> None:
        await _seed_author_role_issues(test_db_session)

        issues, *_ = await _query(
            test_db_session, author_role="maintainer", sort_by="title"
        )

        assert len(issues) == 1
        assert [issue.author for issue in issues] == ["sergio-cazzolato"]

    async def test_contributor_filter_returns_only_contributor(
        self, test_db_session
    ) -> None:
        await _seed_author_role_issues(test_db_session)

        issues, *_ = await _query(
            test_db_session, author_role="contributor", sort_by="title"
        )

        assert len(issues) == 1
        assert [issue.author for issue in issues] == ["craft-contributor"]

    async def test_bot_filter_returns_only_bot(self, test_db_session) -> None:
        await _seed_author_role_issues(test_db_session)

        issues, *_ = await _query(
            test_db_session,
            author_role="bot",
            sort_by="title",
        )

        assert len(issues) == 1
        assert [issue.author for issue in issues] == ["renovate[bot]"]

    async def test_multiple_author_roles_return_combined_matches(
        self, test_db_session
    ) -> None:
        await _seed_author_role_issues(test_db_session)

        issues, *_ = await _query(
            test_db_session,
            author_role="maintainer,contributor",
            sort_by="title",
        )

        assert len(issues) == 2
        assert {issue.author for issue in issues} == {
            "sergio-cazzolato",
            "craft-contributor",
        }


class TestQueryIssuesAuthorRoleColumn:
    async def test_bot_filter_matches_author_is_bot_without_suffix(
        self, test_db_session
    ) -> None:
        await _seed_author_role_column_issues(test_db_session)

        issues, *_ = await _query(
            test_db_session,
            author_role="bot",
            sort_by="title",
        )

        assert [issue.author for issue in issues] == ["renovate[bot]"]

    async def test_bot_filter_excludes_non_bot_maintainer(
        self, test_db_session
    ) -> None:
        await _seed_author_role_column_issues(test_db_session)

        issues, *_ = await _query(
            test_db_session,
            author_role="bot",
            sort_by="title",
        )

        assert "sergio-cazzolato" not in {issue.author for issue in issues}

    async def test_contributor_filter_matches_non_bot_contributor(
        self, test_db_session
    ) -> None:
        await _seed_author_role_column_issues(test_db_session)

        issues, *_ = await _query(
            test_db_session,
            author_role="contributor",
            sort_by="title",
        )

        assert [issue.author for issue in issues] == ["craft-contributor"]


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

        assert authors == ["renovate[bot]", "sergio-cazzolato"]

    async def test_empty_author_role_leaves_query_unfiltered(
        self, test_db_session
    ) -> None:
        await _seed_author_role_column_issues(test_db_session)

        query = _apply_author_role_filter(select(Issue.author), "").order_by(
            Issue.author
        )

        authors = list((await test_db_session.execute(query)).scalars())

        assert authors == ["craft-contributor", "renovate[bot]", "sergio-cazzolato"]


class TestQueryIssuesSearch:
    async def test_search_by_title(self, test_db_session) -> None:
        """Search filter matches issues by title."""
        await _seed_projects_and_issues(test_db_session)

        issues, *_ = await _query(test_db_session, search="core24", sort_by="title")

        assert len(issues) == 1
        assert issues[0]["title"] == "Add support for core24 base"

    async def test_search_by_external_id(self, test_db_session) -> None:
        """Search filter matches issues by external_id."""
        await _seed_projects_and_issues(test_db_session)

        issues, *_ = await _query(test_db_session, search="4", sort_by="title")

        # external_id "4" matches charmcraft issue
        assert any(i["external_id"] == "4" for i in issues)

    async def test_search_no_match(self, test_db_session) -> None:
        """Search filter with no matching term returns empty."""
        await _seed_projects_and_issues(test_db_session)

        issues, *_ = await _query(
            test_db_session, search="nonexistent-xyz", sort_by="title"
        )

        assert len(issues) == 0


class TestQueryIssuesPerPage:
    async def test_items_per_page_limits_results(self, test_db_session) -> None:
        """items_per_page parameter limits the number of returned issues."""
        await _seed_projects_and_issues(test_db_session)

        issues, _, total_pages = await _query(
            test_db_session, items_per_page=2, sort_by="title"
        )

        assert len(issues) == 2
        assert total_pages == 2

    async def test_items_per_page_pagination(self, test_db_session) -> None:
        """Page 2 with items_per_page=2 returns remaining issues."""
        await _seed_projects_and_issues(test_db_session)

        issues, _, total_pages = await _query(
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

        issues, *_ = await _query(test_db_session, sort_by="invalid_field")

        assert len(issues) == 4


class TestQueryIssuesSort:
    async def test_title_sort_returns_alphabetical_order(self, test_db_session) -> None:
        await _seed_sorted_issues(test_db_session)

        issues, *_ = await _query(test_db_session, sort_by="title")

        assert [issue.title for issue in issues] == [
            "Add support for core24 base",
            "Snap refresh fails when revision pinned",
        ]

    async def test_reverse_title_sort_returns_reverse_alphabetical_order(
        self, test_db_session
    ) -> None:
        await _seed_sorted_issues(test_db_session)

        issues, *_ = await _query(test_db_session, sort_by="-title")

        assert [issue.title for issue in issues] == [
            "Snap refresh fails when revision pinned",
            "Add support for core24 base",
        ]

    async def test_age_sort_returns_oldest_first(self, test_db_session) -> None:
        await _seed_sorted_issues(test_db_session)

        issues, *_ = await _query(test_db_session, sort_by="age")

        assert [issue.title for issue in issues] == [
            "Add support for core24 base",
            "Snap refresh fails when revision pinned",
        ]


class TestQueryIssuesIssueType:
    async def test_issue_type_issue_filters_to_issues(self, test_db_session) -> None:
        await _seed_issue_types(test_db_session)

        issues, *_ = await _query(test_db_session, issue_type="issue", sort_by="title")

        assert len(issues) == 1
        assert [issue.issue_type for issue in issues] == ["issue"]

    async def test_issue_type_pull_request_filters_to_prs(
        self, test_db_session
    ) -> None:
        await _seed_issue_types(test_db_session)

        issues, *_ = await _query(
            test_db_session, issue_type="pull_request", sort_by="title"
        )

        assert len(issues) == 1
        assert [issue.issue_type for issue in issues] == ["pull_request"]


async def _seed_issues_with_scores(session) -> None:
    """Seed issues with LLM evaluation scores for testing."""
    project = await _add_project(session, name="snapcraft")

    issue_stale = make_issue(
        project_id=project.id,
        external_id="100",
        title="snapcraft pack fails with LXD backend on Ubuntu 24.04",
        body="When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
        author="craft-contributor",
        created_at=FIXED_NOW - timedelta(days=30),
        updated_at=FIXED_NOW - timedelta(days=30),
        url="https://github.com/canonical/snapcraft/issues/6100",
    )
    session.add(issue_stale)
    await session.flush()
    session.add(
        make_evaluation(
            issue_id=issue_stale.id,
            summary="Stale LXD regression report without recent maintainer follow-up",
            suggested_action="close",
            suggested_action_reason="No activity since the Ubuntu 24.04 migration",
            scores={
                "staleness": 0.95,
                "complexity": 0.3,
                "support_request": 0.2,
                "confidence": 70.0,
            },
        )
    )

    issue_ready = make_issue(
        project_id=project.id,
        external_id="101",
        title="Add support for core24 base",
        body="Please add support for `base: core24` so new snaps can target Ubuntu 24.04.",
        author="sergio-cazzolato",
        created_at=FIXED_NOW - timedelta(days=1),
        updated_at=FIXED_NOW - timedelta(hours=1),
        url="https://github.com/canonical/snapcraft/issues/6101",
    )
    session.add(issue_ready)
    await session.flush()
    session.add(
        make_evaluation(
            issue_id=issue_ready.id,
            summary="Clear enhancement request with maintainers aligned on core24 support",
            suggested_action="work",
            suggested_action_reason="Implementation scope is clear and unblocked",
            scores={
                "staleness": 0.1,
                "complexity": 0.2,
                "support_request": 0.05,
                "confidence": 85.0,
            },
        )
    )

    issue_complex = make_issue(
        project_id=project.id,
        external_id="102",
        title="Refactor manifest parsing for multi-arch builds",
        body="Rework manifest parsing so multi-architecture builds share a consistent metadata pipeline.",
        author="jdoe-canonical",
        created_at=FIXED_NOW - timedelta(days=5),
        updated_at=FIXED_NOW - timedelta(days=2),
        url="https://github.com/canonical/snapcraft/issues/6102",
    )
    session.add(issue_complex)
    await session.flush()
    session.add(
        make_evaluation(
            issue_id=issue_complex.id,
            summary="Large refactor touching manifest parsing and multi-arch build logic",
            suggested_action="investigate",
            suggested_action_reason="Touches multiple build stages and architecture-specific paths",
            scores={
                "staleness": 0.4,
                "complexity": 0.95,
                "support_request": 0.1,
                "confidence": 60.0,
            },
        )
    )

    session.add(
        make_issue(
            project_id=project.id,
            external_id="103",
            title="charmcraft deploy times out on large bundles",
            body="Deploying a large bundle stalls while charmcraft waits for the controller response.",
            author="craft-contributor",
            created_at=FIXED_NOW - timedelta(days=3),
            updated_at=FIXED_NOW - timedelta(days=1),
            url="https://github.com/canonical/charmcraft/issues/6103",
        )
    )

    await session.commit()


class TestQueryIssuesLLMScores:
    """Test that _query_issues properly returns all LLM score fields."""

    async def test_query_returns_all_score_fields(self, test_db_session) -> None:
        """_query_issues should return all LLM score fields for issues."""
        await _seed_issues_with_scores(test_db_session)

        issues, *_ = await _query(test_db_session, sort_by="staleness")

        # Find the issue with scores
        scored_issue = next(
            (issue for issue in issues if issue.external_id == "100"), None
        )
        assert scored_issue is not None

        # Verify all score fields are present
        assert "staleness" in scored_issue
        assert "complexity" in scored_issue
        assert "support_request" in scored_issue
        assert "confidence" in scored_issue
        assert "suggested_action" in scored_issue
        assert "suggested_action_reason" in scored_issue

        # Verify score values
        assert scored_issue.staleness == 0.95
        assert scored_issue.complexity == 0.3
        assert scored_issue.support_request == 0.2
        assert scored_issue.confidence == 70.0
        assert scored_issue.suggested_action == "close"
        assert (
            scored_issue.suggested_action_reason
            == "No activity since the Ubuntu 24.04 migration"
        )

    async def test_query_handles_missing_scores(self, test_db_session) -> None:
        """_query_issues should handle issues without LLM evaluations."""
        await _seed_issues_with_scores(test_db_session)

        issues, *_ = await _query(test_db_session, sort_by="title")

        # Find the issue without scores
        unscored_issue = next(
            (issue for issue in issues if issue.external_id == "103"), None
        )
        assert unscored_issue is not None

        # Verify score fields are None
        assert unscored_issue.staleness is None
        assert unscored_issue.complexity is None
        assert unscored_issue.support_request is None
        assert unscored_issue.confidence is None
        assert unscored_issue.suggested_action is None
        assert unscored_issue.suggested_action_reason is None

    async def test_sort_by_staleness_score(self, test_db_session) -> None:
        """Sort by staleness should order by staleness score descending."""
        await _seed_issues_with_scores(test_db_session)

        issues, *_ = await _query(test_db_session, sort_by="staleness")

        # First issue should be the one with highest staleness
        assert issues[0]["external_id"] == "100"
        assert issues[0]["staleness"] == 0.95

    async def test_sort_by_complexity_score(self, test_db_session) -> None:
        """Sort by complexity should order by complexity score descending."""
        await _seed_issues_with_scores(test_db_session)

        issues, *_ = await _query(test_db_session, sort_by="complexity")

        # First issue should be the one with highest complexity
        assert issues[0]["external_id"] == "102"
        assert issues[0]["complexity"] == 0.95

    async def test_sort_by_support_request_score(self, test_db_session) -> None:
        """Sort by support_request should order by support_request score descending."""
        await _seed_issues_with_scores(test_db_session)

        issues, *_ = await _query(test_db_session, sort_by="support_request")

        # First issue should be the one with highest support_request
        assert issues[0]["external_id"] == "100"
        assert issues[0]["support_request"] == 0.2

    async def test_sort_by_confidence_score(self, test_db_session) -> None:
        """Sort by confidence should order by confidence score descending."""
        await _seed_issues_with_scores(test_db_session)

        issues, *_ = await _query(test_db_session, sort_by="confidence")

        # First issue should be the one with highest confidence
        assert issues[0]["external_id"] == "101"
        assert issues[0]["confidence"] == 85.0


class TestLLMStatusFilter:
    """Tests for the llm_status filter parameter."""

    async def test_no_llm_filter_returns_unevaluated_issues(
        self, test_db_session
    ) -> None:
        """llm_status=no_llm should return only issues without LLM evaluations."""
        await _seed_projects_and_issues(test_db_session)
        await test_db_session.commit()

        # All seeded issues have no LLM evaluations
        issues, *_ = await _query(test_db_session, llm_status="no_llm")
        assert len(issues) > 0

        # Now add an evaluation for one issue
        issue_result = await test_db_session.execute(
            select(Issue).where(Issue.external_id == "1")
        )
        issue = issue_result.scalar_one()
        test_db_session.add(
            make_evaluation(
                issue_id=issue.id,
                model_name="test",
                summary="A summary",
                suggested_action="keep_open",
                suggested_action_reason="reason",
                scores={"staleness": 0.5},
            )
        )
        await test_db_session.commit()

        issues_after, *_ = await _query(test_db_session, llm_status="no_llm")
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
            make_evaluation(
                issue_id=issue.id,
                model_name="test",
                summary="",
                suggested_action="keep_open",
                suggested_action_reason="reason",
                scores={"staleness": 0.5},
            )
        )
        await test_db_session.commit()

        issues, *_ = await _query(test_db_session, llm_status="partial_llm")
        ids = [i["external_id"] for i in issues]
        assert "1" in ids

    async def test_empty_llm_status_returns_all(self, test_db_session) -> None:
        """Empty llm_status should not filter anything."""
        await _seed_projects_and_issues(test_db_session)
        await test_db_session.commit()

        all_issues, *_ = await _query(test_db_session, llm_status="")
        no_filter, *_ = await _query(test_db_session)
        assert len(all_issues) == len(no_filter)


class TestFindSimilarIssues:
    """Tests for IssueRepository.find_similar_issues."""

    async def test_find_similar_issues_returns_empty_when_no_embedding(
        self, test_db_session
    ) -> None:
        """Returns [] if the current issue has no embedding (always in SQLite tests)."""
        project = make_project()
        test_db_session.add(project)
        await test_db_session.flush()

        issue = make_issue(project_id=project.id, external_id="10")
        test_db_session.add(issue)
        await test_db_session.flush()

        test_db_session.add(make_evaluation(issue_id=issue.id, latest=True))
        await test_db_session.flush()

        repo = IssueRepository(test_db_session)
        result = await repo.find_similar_issues(issue_id=issue.id)
        assert result == []

    async def test_find_similar_issues_returns_empty_for_unknown_issue(
        self, test_db_session
    ) -> None:
        """Returns [] if the issue has no evaluation row."""
        repo = IssueRepository(test_db_session)
        result = await repo.find_similar_issues(issue_id=99999)
        assert result == []
