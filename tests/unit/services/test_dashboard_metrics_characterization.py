"""Characterization tests for ``DashboardService.get_homepage_metrics``.

These tests pin down what ``get_homepage_metrics`` *currently does*, not what
it arguably should do. They exist so the Phase 4b refactor (splitting the god
modules, unifying issue filtering, introducing view models) can prove it
changed nothing observable.

Where behaviour looks wrong, it is pinned anyway and flagged with a
``# NOTE: looks suspect`` comment rather than fixed.
"""

from datetime import UTC, datetime, timedelta

import pytest
from craft_dashboard.config import DashboardConfig
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.models.release import Release
from craft_dashboard.services.dashboard_service import DashboardService
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import make_issue

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"

_idx = next(
    (
        i
        for i in LLMEvaluation.__table__.indexes
        if i.name == "ix_llm_evaluations_latest_issue"
    ),
    None,
)
if _idx is not None:
    _idx.dialect_options.pop("postgresql", None)


NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


async def _add_project(
    session: AsyncSession,
    name: str = "snapcraft",
    category: str = "application",
    display_order: int = 1,
) -> Project:
    """Insert and flush a project, returning it with its assigned id."""
    project = Project(
        name=name,
        category=category,
        github_org="canonical",
        display_order=display_order,
    )
    session.add(project)
    await session.flush()
    return project


async def _metrics(
    session: AsyncSession,
    config: DashboardConfig | None = None,
    now: datetime | None = NOW,
) -> dict:
    await session.flush()
    service = DashboardService(session)
    return await service.get_homepage_metrics(config or DashboardConfig(), now=now)


# ---------------------------------------------------------------------------
# "now" derivation
# ---------------------------------------------------------------------------


class TestNowDerivation:
    """Pin how the reference timestamp is obtained and normalized."""

    async def test_naive_now_is_interpreted_as_utc_not_local_time(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="pull_request",
                author_is_maintainer=False,
                author_is_bot=False,
                created_at=NOW - timedelta(days=10),
            )
        )

        naive = NOW.replace(tzinfo=None)
        aware = await _metrics(test_db_session, now=NOW)
        naive_metrics = await _metrics(test_db_session, now=naive)

        assert naive_metrics["velocity"] == aware["velocity"]
        assert naive_metrics["velocity"]["overall_avg_age"] == 10

    async def test_now_defaults_to_wall_clock_when_omitted(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="pull_request",
                created_at=datetime.now(tz=UTC) - timedelta(days=7),
            )
        )

        metrics = await _metrics(test_db_session, now=None)

        assert metrics["velocity"]["overall_avg_age"] == 7


# ---------------------------------------------------------------------------
# Date window boundaries
# ---------------------------------------------------------------------------


class TestDateWindowBoundaries:
    """Pin the inclusive/exclusive edges of the 30-day and 365-day windows."""

    @pytest.mark.parametrize(
        ("offset", "expected"),
        [
            (timedelta(days=30), 1),
            (timedelta(days=30, seconds=1), 0),
            (timedelta(days=29, hours=23), 1),
        ],
    )
    async def test_thirty_day_close_window_lower_edge_is_inclusive(
        self, test_db_session: AsyncSession, offset: timedelta, expected: int
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                state="closed",
                created_at=NOW - timedelta(days=200),
                closed_at=NOW - offset,
            )
        )

        metrics = await _metrics(test_db_session)

        assert metrics["throughput"]["issues_30d"] == expected

    @pytest.mark.parametrize(
        ("offset", "expected"),
        [
            (timedelta(days=365), 1),
            (timedelta(days=365, seconds=1), 0),
        ],
    )
    async def test_year_close_window_lower_edge_is_inclusive(
        self, test_db_session: AsyncSession, offset: timedelta, expected: int
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                state="closed",
                created_at=NOW - timedelta(days=500),
                closed_at=NOW - offset,
            )
        )

        metrics = await _metrics(test_db_session)

        assert metrics["throughput"]["issues_365d"] == expected

    async def test_close_windows_have_no_upper_bound_so_future_closes_count(
        self, test_db_session: AsyncSession
    ) -> None:
        # NOTE: looks suspect -- the windows are half-open (`closed_at >= cutoff`)
        # with no upper bound, so an item with a closed_at in the future is
        # counted in both the 30-day and 365-day throughput figures.
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                state="closed",
                created_at=NOW - timedelta(days=5),
                closed_at=NOW + timedelta(days=400),
            )
        )

        metrics = await _metrics(test_db_session)

        assert metrics["throughput"]["issues_30d"] == 1
        assert metrics["throughput"]["issues_365d"] == 1

    @pytest.mark.parametrize(
        ("offset", "expected"),
        [
            (timedelta(days=30), 1),
            (timedelta(days=30, seconds=1), 0),
        ],
    )
    async def test_created_in_last_thirty_days_lower_edge_is_inclusive(
        self, test_db_session: AsyncSession, offset: timedelta, expected: int
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                state="open",
                created_at=NOW - offset,
            )
        )

        metrics = await _metrics(test_db_session)

        assert metrics["volume"]["created_issues_30d"] == expected
        assert metrics["untriaged"]["issues_30d_new"] == expected

    async def test_ages_are_whole_days_truncated_not_rounded(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="pull_request",
                created_at=NOW - timedelta(days=9, hours=23, minutes=59),
            )
        )

        metrics = await _metrics(test_db_session)

        assert metrics["velocity"]["overall_avg_age"] == 9

    async def test_future_created_at_is_clamped_to_zero_days_old(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="pull_request",
                author_is_maintainer=False,
                author_is_bot=False,
                created_at=NOW + timedelta(days=5),
            )
        )

        metrics = await _metrics(test_db_session)

        assert metrics["velocity"]["overall_avg_age"] == 0
        assert metrics["aging_prs"][0]["days_old"] == 0


# ---------------------------------------------------------------------------
# Project count
# ---------------------------------------------------------------------------


class TestProjectCount:
    """Pin which projects are counted on the homepage."""

    async def test_project_count_excludes_aggregate_category(
        self, test_db_session: AsyncSession
    ) -> None:
        await _add_project(test_db_session, "snapcraft", "application", 1)
        await _add_project(test_db_session, "craft-parts", "library", 2)
        await _add_project(test_db_session, "all-projects", "aggregate", 3)

        metrics = await _metrics(test_db_session)

        assert metrics["project_count"] == 2

    async def test_project_count_is_zero_when_no_projects_exist(
        self, test_db_session: AsyncSession
    ) -> None:
        metrics = await _metrics(test_db_session)

        assert metrics["project_count"] == 0


# ---------------------------------------------------------------------------
# PR velocity
# ---------------------------------------------------------------------------


class TestVelocity:
    """Pin PR age, first-response and 12-month baseline arithmetic."""

    async def test_velocity_is_all_none_when_there_are_no_open_prs(
        self, test_db_session: AsyncSession
    ) -> None:
        await _add_project(test_db_session)

        velocity = (await _metrics(test_db_session))["velocity"]

        assert velocity == {
            "contributor_count": 0,
            "contributor_avg_age": None,
            "contributor_avg_delta": None,
            "contributor_avg_baseline": None,
            "first_response_waiting_count": 0,
            "first_response_avg_days": None,
            "first_response_avg_delta": None,
            "first_response_avg_baseline": None,
            "overall_count": 0,
            "overall_avg_age": None,
            "overall_avg_delta": None,
            "overall_avg_baseline": None,
        }

    async def test_maintainer_and_bot_prs_count_only_towards_overall_age(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add_all(
            [
                make_issue(
                    project_id=project.id,
                    external_id="1",
                    issue_type="pull_request",
                    author_is_maintainer=True,
                    created_at=NOW - timedelta(days=100),
                ),
                make_issue(
                    project_id=project.id,
                    external_id="2",
                    issue_type="pull_request",
                    author_is_bot=True,
                    created_at=NOW - timedelta(days=50),
                ),
            ]
        )

        velocity = (await _metrics(test_db_session))["velocity"]

        assert velocity["overall_count"] == 2
        assert velocity["overall_avg_age"] == 75
        assert velocity["contributor_count"] == 0
        assert velocity["contributor_avg_age"] is None
        assert velocity["first_response_waiting_count"] == 0

    async def test_contributor_pr_with_maintainer_comment_is_not_awaiting_response(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="pull_request",
                created_at=NOW - timedelta(days=20),
                comments=[
                    {"author": "maint", "created_at": (NOW).isoformat(), "body": "hi"}
                ],
            )
        )
        config = DashboardConfig(maintainers=["maint"])

        velocity = (await _metrics(test_db_session, config))["velocity"]

        assert velocity["contributor_count"] == 1
        assert velocity["first_response_waiting_count"] == 0

    async def test_launchpad_maintainers_also_count_as_a_first_response(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="pull_request",
                created_at=NOW - timedelta(days=20),
                comments=[{"author": "lp-maint", "created_at": NOW.isoformat()}],
            )
        )
        config = DashboardConfig(launchpad_maintainers=["lp-maint"])

        velocity = (await _metrics(test_db_session, config))["velocity"]

        assert velocity["first_response_waiting_count"] == 0

    async def test_nonzero_review_count_suppresses_awaiting_response(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="pull_request",
                created_at=NOW - timedelta(days=20),
                metadata_={"review_count": 1},
            )
        )

        velocity = (await _metrics(test_db_session))["velocity"]

        assert velocity["contributor_count"] == 1
        assert velocity["first_response_waiting_count"] == 0

    async def test_non_maintainer_comments_alone_leave_pr_awaiting_response(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="pull_request",
                created_at=NOW - timedelta(days=20),
                comments=[{"author": "random-person", "created_at": NOW.isoformat()}],
            )
        )
        config = DashboardConfig(maintainers=["maint"])

        velocity = (await _metrics(test_db_session, config))["velocity"]

        assert velocity["first_response_waiting_count"] == 1
        assert velocity["first_response_avg_days"] == 20

    async def test_baseline_averages_twelve_monthly_checkpoints_of_open_pr_age(
        self, test_db_session: AsyncSession
    ) -> None:
        # A PR open for 400 days is alive at every checkpoint (now - 30*i for
        # i in 1..12), so its age at those checkpoints is 400-30i days. The
        # mean of those twelve values is exactly 205.
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="pull_request",
                created_at=NOW - timedelta(days=400),
            )
        )

        velocity = (await _metrics(test_db_session))["velocity"]

        assert velocity["contributor_avg_age"] == 400
        assert velocity["contributor_avg_baseline"] == 205
        assert velocity["contributor_avg_delta"] == 195
        assert velocity["overall_avg_baseline"] == 205
        assert velocity["first_response_avg_baseline"] == 205

    async def test_recent_prs_produce_no_baseline_and_therefore_no_delta(
        self, test_db_session: AsyncSession
    ) -> None:
        # NOTE: looks suspect -- a PR younger than 30 days exists at no
        # checkpoint, so the baseline (and delta) silently become None rather
        # than 0, and the UI loses the comparison entirely.
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="pull_request",
                created_at=NOW - timedelta(days=5),
            )
        )

        velocity = (await _metrics(test_db_session))["velocity"]

        assert velocity["contributor_avg_age"] == 5
        assert velocity["contributor_avg_baseline"] is None
        assert velocity["contributor_avg_delta"] is None

    async def test_baseline_checkpoints_use_thirty_day_months_not_calendar_months(
        self, test_db_session: AsyncSession
    ) -> None:
        # NOTE: looks suspect -- checkpoints are `now - timedelta(days=30*i)`,
        # so the twelfth checkpoint is 360 days back, not 365. A PR closed
        # between 360 and 365 days ago is loaded by the history query but
        # contributes to no checkpoint.
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="pull_request",
                state="closed",
                created_at=NOW - timedelta(days=500),
                closed_at=NOW - timedelta(days=362),
            )
        )

        velocity = (await _metrics(test_db_session))["velocity"]

        assert velocity["contributor_avg_baseline"] is None

    async def test_unparseable_comment_timestamp_counts_as_a_response_in_baseline(
        self, test_db_session: AsyncSession
    ) -> None:
        # NOTE: looks suspect -- a malformed `created_at` on a maintainer
        # comment is treated as "responded", so bad data improves the metric.
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="pull_request",
                created_at=NOW - timedelta(days=400),
                comments=[{"author": "maint", "created_at": "not-a-date"}],
            )
        )
        config = DashboardConfig(maintainers=["maint"])

        velocity = (await _metrics(test_db_session, config))["velocity"]

        assert velocity["contributor_avg_baseline"] == 205
        assert velocity["first_response_avg_baseline"] is None


# ---------------------------------------------------------------------------
# Resolution throughput
# ---------------------------------------------------------------------------


class TestThroughput:
    """Pin closure throughput counting and monthly-average rounding."""

    async def test_merged_prs_count_as_closed_throughput(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="pull_request",
                state="merged",
                created_at=NOW - timedelta(days=20),
                closed_at=NOW - timedelta(days=2),
            )
        )

        throughput = (await _metrics(test_db_session))["throughput"]

        assert throughput["prs_30d"] == 1
        assert throughput["total_30d"] == 1

    async def test_closed_row_without_closed_at_is_not_counted(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                state="closed",
                created_at=NOW - timedelta(days=20),
                closed_at=None,
            )
        )

        throughput = (await _metrics(test_db_session))["throughput"]

        assert throughput["issues_30d"] == 0
        assert throughput["issues_365d"] == 0

    async def test_monthly_average_uses_bankers_rounding_down_on_a_half(
        self, test_db_session: AsyncSession
    ) -> None:
        # NOTE: looks suspect -- 6/12 == 0.5 rounds to 0 under Python's
        # round-half-to-even, so six closures in a year report a zero average.
        project = await _add_project(test_db_session)
        for n in range(6):
            test_db_session.add(
                make_issue(
                    project_id=project.id,
                    external_id=str(n),
                    state="closed",
                    created_at=NOW - timedelta(days=300),
                    closed_at=NOW - timedelta(days=100),
                )
            )

        throughput = (await _metrics(test_db_session))["throughput"]

        assert throughput["total_365d"] == 6
        assert throughput["total_monthly_avg"] == 0
        assert throughput["total_monthly_delta"] == 0

    async def test_monthly_average_rounds_a_one_point_five_half_up_to_two(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        for n in range(18):
            test_db_session.add(
                make_issue(
                    project_id=project.id,
                    external_id=str(n),
                    state="closed",
                    created_at=NOW - timedelta(days=300),
                    closed_at=NOW - timedelta(days=100),
                )
            )

        throughput = (await _metrics(test_db_session))["throughput"]

        assert throughput["total_365d"] == 18
        assert throughput["total_monthly_avg"] == 2
        assert throughput["total_monthly_delta"] == -2

    async def test_thirty_day_closures_are_also_inside_the_year_window(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                state="closed",
                created_at=NOW - timedelta(days=20),
                closed_at=NOW - timedelta(days=1),
            )
        )

        throughput = (await _metrics(test_db_session))["throughput"]

        assert throughput["issues_30d"] == 1
        assert throughput["issues_365d"] == 1


# ---------------------------------------------------------------------------
# Untriaged backlog
# ---------------------------------------------------------------------------


class TestUntriagedBacklog:
    """Pin what counts as untriaged for issues and for pull requests."""

    async def _seed_issue_with_action(
        self,
        session: AsyncSession,
        project: Project,
        external_id: str,
        action: str | None,
        issue_type: str = "issue",
    ) -> None:
        issue = make_issue(
            project_id=project.id,
            external_id=external_id,
            issue_type=issue_type,
            created_at=NOW - timedelta(days=5),
        )
        session.add(issue)
        await session.flush()
        session.add(
            LLMEvaluation(
                issue_id=issue.id,
                model_name="m",
                summary="s",
                suggested_action=action,
                scores={},
                evaluated_at=NOW,
                latest=True,
                eval_type="scoring",
            )
        )

    async def test_unevaluated_open_issue_counts_as_untriaged(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                created_at=NOW - timedelta(days=5),
            )
        )

        untriaged = (await _metrics(test_db_session))["untriaged"]

        assert untriaged["issues_count"] == 1
        assert untriaged["issues_30d_new"] == 1

    async def test_issue_evaluated_with_null_action_counts_as_untriaged(
        self, test_db_session: AsyncSession
    ) -> None:
        # NOTE: looks suspect -- a summary-only evaluation (no suggested
        # action) is indistinguishable from "never evaluated" here.
        project = await _add_project(test_db_session)
        await self._seed_issue_with_action(test_db_session, project, "1", None)

        untriaged = (await _metrics(test_db_session))["untriaged"]

        assert untriaged["issues_count"] == 1

    async def test_issue_actioned_as_needs_triage_counts_as_untriaged(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        await self._seed_issue_with_action(
            test_db_session, project, "1", "needs_triage"
        )

        untriaged = (await _metrics(test_db_session))["untriaged"]

        assert untriaged["issues_count"] == 1

    async def test_issue_with_any_other_action_is_considered_triaged(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        await self._seed_issue_with_action(test_db_session, project, "1", "keep_open")

        untriaged = (await _metrics(test_db_session))["untriaged"]

        assert untriaged["issues_count"] == 0

    async def test_prs_use_needs_review_rather_than_needs_triage(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        await self._seed_issue_with_action(
            test_db_session, project, "1", "needs_review", "pull_request"
        )
        await self._seed_issue_with_action(
            test_db_session, project, "2", "needs_triage", "pull_request"
        )

        untriaged = (await _metrics(test_db_session))["untriaged"]

        assert untriaged["prs_count"] == 1
        assert untriaged["issues_count"] == 0

    async def test_non_latest_evaluation_does_not_triage_an_issue(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        issue = make_issue(
            project_id=project.id,
            external_id="1",
            created_at=NOW - timedelta(days=5),
        )
        test_db_session.add(issue)
        await test_db_session.flush()
        test_db_session.add(
            LLMEvaluation(
                issue_id=issue.id,
                model_name="m",
                summary="s",
                suggested_action="keep_open",
                scores={},
                evaluated_at=NOW,
                latest=False,
                eval_type="scoring",
            )
        )

        untriaged = (await _metrics(test_db_session))["untriaged"]

        assert untriaged["issues_count"] == 1

    async def test_closed_issues_are_never_untriaged(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                state="closed",
                created_at=NOW - timedelta(days=5),
                closed_at=NOW - timedelta(days=1),
            )
        )

        untriaged = (await _metrics(test_db_session))["untriaged"]

        assert untriaged["issues_count"] == 0


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------


class TestVolume:
    """Pin all-time volume counting and the 30-day net figures."""

    async def test_merged_prs_are_folded_into_closed_prs(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add_all(
            [
                make_issue(
                    project_id=project.id,
                    external_id="1",
                    issue_type="pull_request",
                    state="merged",
                    created_at=NOW - timedelta(days=100),
                    closed_at=NOW - timedelta(days=90),
                ),
                make_issue(
                    project_id=project.id,
                    external_id="2",
                    issue_type="pull_request",
                    state="closed",
                    created_at=NOW - timedelta(days=100),
                    closed_at=NOW - timedelta(days=90),
                ),
            ]
        )

        volume = (await _metrics(test_db_session))["volume"]

        assert volume["closed_prs"] == 2
        assert volume["open_prs"] == 0
        assert volume["total_items"] == 2

    async def test_unrecognised_states_are_dropped_from_total_items(
        self, test_db_session: AsyncSession
    ) -> None:
        # NOTE: looks suspect -- total_items only sums the four known
        # (type, state) buckets, so e.g. an issue in state "merged" vanishes
        # from the all-time totals rather than raising or being counted.
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="issue",
                state="merged",
                created_at=NOW - timedelta(days=100),
            )
        )

        volume = (await _metrics(test_db_session))["volume"]

        assert volume["total_items"] == 0
        assert volume["open_issues"] == 0
        assert volume["closed_issues"] == 0

    async def test_net_open_is_created_minus_closed_in_the_same_window(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        for n in range(3):
            test_db_session.add(
                make_issue(
                    project_id=project.id,
                    external_id=f"new-{n}",
                    created_at=NOW - timedelta(days=2),
                )
            )
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="closed",
                state="closed",
                created_at=NOW - timedelta(days=200),
                closed_at=NOW - timedelta(days=2),
            )
        )

        volume = (await _metrics(test_db_session))["volume"]

        assert volume["created_issues_30d"] == 3
        assert volume["issues_30d_closed"] == 1
        assert volume["net_open_issues_30d"] == 2

    async def test_open_thirty_day_keys_are_aliases_of_the_net_keys(
        self, test_db_session: AsyncSession
    ) -> None:
        # NOTE: looks suspect -- `open_issues_30d`/`open_prs_30d`/
        # `open_total_30d` are assigned the *net change* values, not an open
        # count. The names promise something the values do not deliver.
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                created_at=NOW - timedelta(days=2),
            )
        )

        volume = (await _metrics(test_db_session))["volume"]

        assert volume["open_issues_30d"] == volume["net_open_issues_30d"]
        assert volume["open_prs_30d"] == volume["net_open_prs_30d"]
        assert volume["open_total_30d"] == volume["net_open_total_30d"]

    async def test_volume_ignores_projects_in_the_aggregate_category(
        self, test_db_session: AsyncSession
    ) -> None:
        aggregate = await _add_project(test_db_session, "all-projects", "aggregate", 1)
        test_db_session.add(
            make_issue(
                project_id=aggregate.id,
                external_id="1",
                created_at=NOW - timedelta(days=2),
            )
        )

        volume = (await _metrics(test_db_session))["volume"]

        assert volume["total_items"] == 0
        assert volume["created_issues_30d"] == 0


# ---------------------------------------------------------------------------
# Release spotlight
# ---------------------------------------------------------------------------


class TestReleaseSpotlight:
    """Pin least-recent-application release selection, fallbacks and order."""

    async def test_only_applications_appear_in_the_spotlight(
        self, test_db_session: AsyncSession
    ) -> None:
        await _add_project(test_db_session, "snapcraft", "application", 1)
        await _add_project(test_db_session, "craft-parts", "library", 2)

        metrics = await _metrics(test_db_session)

        assert [a["project_name"] for a in metrics["least_recent_apps"]] == [
            "snapcraft"
        ]

    async def test_hidden_releases_are_excluded_from_the_spotlight(
        self, test_db_session: AsyncSession
    ) -> None:
        await _add_project(test_db_session, "snapcraft", "application", 1)
        config = DashboardConfig(hide_releases=["snapcraft"])

        metrics = await _metrics(test_db_session, config)

        assert metrics["least_recent_apps"] == []

    async def test_application_with_no_release_or_fallback_is_unreleased(
        self, test_db_session: AsyncSession
    ) -> None:
        await _add_project(test_db_session, "debcraft", "application", 1)

        entry = (await _metrics(test_db_session))["least_recent_apps"][0]

        assert entry["version"] == "(unreleased)"
        assert entry["released_at"] is None
        assert entry["days_ago"] is None
        assert entry["badge_color"] == "neutral"
        assert entry["is_fallback"] is True

    async def test_configured_fallback_tag_reports_is_fallback_false(
        self, test_db_session: AsyncSession
    ) -> None:
        # NOTE: looks suspect -- `is_fallback` is True only when the fallback
        # *tag* is literally "(unreleased)". A configured fallback tag such as
        # "1.0" is still a fallback, yet reports is_fallback=False.
        await _add_project(test_db_session, "debcraft", "application", 1)
        config = DashboardConfig(
            initial_release_dates={"debcraft": "2026-05-16"},
            initial_release_tags={"debcraft": "1.0"},
        )

        entry = (await _metrics(test_db_session, config))["least_recent_apps"][0]

        assert entry["version"] == "1.0"
        assert entry["days_ago"] == 30
        assert entry["badge_color"] == "green"
        assert entry["is_fallback"] is False

    async def test_unparseable_fallback_date_falls_through_to_unreleased(
        self, test_db_session: AsyncSession
    ) -> None:
        await _add_project(test_db_session, "debcraft", "application", 1)
        config = DashboardConfig(
            initial_release_dates={"debcraft": "not-a-date"},
            initial_release_tags={"debcraft": "1.0"},
        )

        entry = (await _metrics(test_db_session, config))["least_recent_apps"][0]

        assert entry["version"] == "(unreleased)"
        assert entry["is_fallback"] is True

    async def test_real_release_takes_precedence_over_configured_fallback(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session, "snapcraft", "application", 1)
        test_db_session.add(
            Release(
                project_id=project.id,
                version="8.4.0",
                released_at=NOW - timedelta(days=45),
            )
        )
        config = DashboardConfig(
            initial_release_dates={"snapcraft": "2020-01-01"},
            initial_release_tags={"snapcraft": "0.1"},
        )

        entry = (await _metrics(test_db_session, config))["least_recent_apps"][0]

        assert entry["version"] == "8.4.0"
        assert entry["days_ago"] == 45
        assert entry["badge_color"] == "yellow"

    async def test_spotlight_sorts_oldest_first_with_unreleased_apps_last(
        self, test_db_session: AsyncSession
    ) -> None:
        recent = await _add_project(test_db_session, "rockcraft", "application", 1)
        stale = await _add_project(test_db_session, "snapcraft", "application", 2)
        await _add_project(test_db_session, "debcraft", "application", 3)
        test_db_session.add_all(
            [
                Release(
                    project_id=recent.id,
                    version="1.0",
                    released_at=NOW - timedelta(days=5),
                ),
                Release(
                    project_id=stale.id,
                    version="2.0",
                    released_at=NOW - timedelta(days=200),
                ),
            ]
        )

        entries = (await _metrics(test_db_session))["least_recent_apps"]

        assert [e["project_name"] for e in entries] == [
            "snapcraft",
            "rockcraft",
            "debcraft",
        ]

    async def test_latest_release_is_the_one_with_the_newest_released_at(
        self, test_db_session: AsyncSession
    ) -> None:
        # Releases are unique per (project_id, branch), so "latest per project"
        # means newest released_at across all branches.
        project = await _add_project(test_db_session, "snapcraft", "application", 1)
        test_db_session.add_all(
            [
                Release(
                    project_id=project.id,
                    version="7.0",
                    branch="7.x",
                    released_at=NOW - timedelta(days=200),
                ),
                Release(
                    project_id=project.id,
                    version="8.0",
                    branch="main",
                    released_at=NOW - timedelta(days=10),
                ),
            ]
        )

        entry = (await _metrics(test_db_session))["least_recent_apps"][0]

        assert entry["version"] == "8.0"


# ---------------------------------------------------------------------------
# Spotlight lists
# ---------------------------------------------------------------------------


class TestAgingPRs:
    """Pin the aging contributor PR list."""

    async def test_aging_prs_returns_the_five_oldest_contributor_prs(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        for n in range(6):
            test_db_session.add(
                make_issue(
                    project_id=project.id,
                    external_id=str(n),
                    issue_type="pull_request",
                    created_at=NOW - timedelta(days=100 - n),
                )
            )

        aging = (await _metrics(test_db_session))["aging_prs"]

        assert len(aging) == 5
        assert [p["external_id"] for p in aging] == ["0", "1", "2", "3", "4"]
        assert aging[0]["days_old"] == 100

    async def test_aging_prs_excludes_maintainer_and_bot_authors(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add_all(
            [
                make_issue(
                    project_id=project.id,
                    external_id="m",
                    issue_type="pull_request",
                    author_is_maintainer=True,
                    created_at=NOW - timedelta(days=100),
                ),
                make_issue(
                    project_id=project.id,
                    external_id="b",
                    issue_type="pull_request",
                    author_is_bot=True,
                    created_at=NOW - timedelta(days=99),
                ),
            ]
        )

        assert (await _metrics(test_db_session))["aging_prs"] == []

    async def test_aging_prs_excludes_closed_prs(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="pull_request",
                state="closed",
                created_at=NOW - timedelta(days=100),
                closed_at=NOW - timedelta(days=1),
            )
        )

        assert (await _metrics(test_db_session))["aging_prs"] == []


class TestNeedsTriageList:
    """Pin the needs-triage issue spotlight."""

    async def test_needs_triage_returns_the_five_oldest_untriaged_issues(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        for n in range(6):
            test_db_session.add(
                make_issue(
                    project_id=project.id,
                    external_id=str(n),
                    created_at=NOW - timedelta(days=100 - n),
                )
            )

        rows = (await _metrics(test_db_session))["needs_triage_issues"]

        assert [r["external_id"] for r in rows] == ["0", "1", "2", "3", "4"]
        assert rows[0]["days_old"] == 100

    async def test_needs_triage_list_never_contains_pull_requests(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="pull_request",
                created_at=NOW - timedelta(days=100),
            )
        )

        assert (await _metrics(test_db_session))["needs_triage_issues"] == []


class TestQuickWins:
    """Pin quick-win selection, threshold and ordering."""

    async def _add_scored_issue(
        self,
        session: AsyncSession,
        project: Project,
        external_id: str,
        scores: dict,
        *,
        latest: bool = True,
        state: str = "open",
        issue_type: str = "issue",
    ) -> None:
        issue = make_issue(
            project_id=project.id,
            external_id=external_id,
            state=state,
            issue_type=issue_type,
            created_at=NOW - timedelta(days=10),
        )
        session.add(issue)
        await session.flush()
        session.add(
            LLMEvaluation(
                issue_id=issue.id,
                model_name="m",
                summary="s",
                suggested_action="keep_open",
                scores=scores,
                evaluated_at=NOW,
                latest=latest,
                eval_type="scoring",
            )
        )

    async def test_quick_wins_are_sorted_by_descending_score_and_capped_at_five(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        for n, score in enumerate([55, 95, 75, 85, 65, 60]):
            await self._add_scored_issue(
                test_db_session, project, str(n), {"quick_win": score}
            )

        wins = (await _metrics(test_db_session))["quick_wins"]

        assert [w["quick_win"] for w in wins] == [95, 85, 75, 65, 60]

    async def test_quick_win_threshold_of_fifty_is_inclusive(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        await self._add_scored_issue(test_db_session, project, "at", {"quick_win": 50})
        await self._add_scored_issue(
            test_db_session, project, "below", {"quick_win": 49}
        )

        wins = (await _metrics(test_db_session))["quick_wins"]

        assert [w["external_id"] for w in wins] == ["at"]

    async def test_float_quick_win_scores_are_rounded_half_to_even_before_compare(
        self, test_db_session: AsyncSession
    ) -> None:
        # NOTE: looks suspect -- 49.5 rounds *up* to 50 and qualifies, while
        # 50.5 rounds *down* to 50; the threshold behaves non-monotonically
        # around halves because of round-half-to-even.
        project = await _add_project(test_db_session)
        await self._add_scored_issue(
            test_db_session, project, "up", {"quick_win": 49.5}
        )
        await self._add_scored_issue(
            test_db_session, project, "down", {"quick_win": 50.5}
        )

        wins = (await _metrics(test_db_session))["quick_wins"]

        assert sorted(w["quick_win"] for w in wins) == [50, 50]

    async def test_non_numeric_quick_win_score_is_coerced_to_zero_and_dropped(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        await self._add_scored_issue(
            test_db_session, project, "1", {"quick_win": "very high"}
        )

        assert (await _metrics(test_db_session))["quick_wins"] == []

    async def test_evaluation_without_a_quick_win_key_is_skipped(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        await self._add_scored_issue(test_db_session, project, "1", {"impact": 0.9})

        assert (await _metrics(test_db_session))["quick_wins"] == []

    async def test_quick_wins_carry_impact_and_complexity_verbatim(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        await self._add_scored_issue(
            test_db_session,
            project,
            "1",
            {"quick_win": 90, "impact": 0.8, "complexity": 0.2},
        )

        win = (await _metrics(test_db_session))["quick_wins"][0]

        assert win["impact"] == 0.8
        assert win["complexity"] == 0.2

    async def test_quick_wins_ignore_pull_requests_and_closed_issues(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        await self._add_scored_issue(
            test_db_session,
            project,
            "pr",
            {"quick_win": 90},
            issue_type="pull_request",
        )
        await self._add_scored_issue(
            test_db_session, project, "closed", {"quick_win": 90}, state="closed"
        )

        assert (await _metrics(test_db_session))["quick_wins"] == []

    async def test_quick_wins_ignore_superseded_evaluations(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        await self._add_scored_issue(
            test_db_session, project, "1", {"quick_win": 90}, latest=False
        )

        assert (await _metrics(test_db_session))["quick_wins"] == []


# ---------------------------------------------------------------------------
# Project health rows
# ---------------------------------------------------------------------------


class TestProjectHealthRows:
    """Pin per-project health rows, bucketing and badge classification."""

    async def test_projects_are_bucketed_by_their_database_category(
        self, test_db_session: AsyncSession
    ) -> None:
        await _add_project(test_db_session, "snapcraft", "application", 1)
        await _add_project(test_db_session, "craft-parts", "library", 2)
        await _add_project(test_db_session, "misc", "tooling", 3)

        metrics = await _metrics(test_db_session)

        assert [p["name"] for p in metrics["application_projects"]] == ["snapcraft"]
        assert [p["name"] for p in metrics["library_projects"]] == ["craft-parts"]
        assert [p["name"] for p in metrics["other_projects"]] == ["misc"]

    async def test_config_category_lists_override_the_database_category(
        self, test_db_session: AsyncSession
    ) -> None:
        await _add_project(test_db_session, "snapcraft", "application", 1)
        config = DashboardConfig(craft_libraries=["snapcraft"])

        metrics = await _metrics(test_db_session, config)

        assert metrics["application_projects"] == []
        assert [p["name"] for p in metrics["library_projects"]] == ["snapcraft"]
        # The row still reports its *database* category, not the bucket it
        # was placed in.
        assert metrics["library_projects"][0]["category"] == "application"

    async def test_snapcraft_launchpad_is_hardcoded_into_the_other_bucket(
        self, test_db_session: AsyncSession
    ) -> None:
        # NOTE: looks suspect -- a project name is special-cased in service
        # code rather than driven by configuration.
        await _add_project(test_db_session, "snapcraft (launchpad)", "application", 1)

        metrics = await _metrics(test_db_session)

        assert metrics["application_projects"] == []
        assert [p["name"] for p in metrics["other_projects"]] == [
            "snapcraft (launchpad)"
        ]

    async def test_project_rows_follow_display_order(
        self, test_db_session: AsyncSession
    ) -> None:
        await _add_project(test_db_session, "rockcraft", "application", 2)
        await _add_project(test_db_session, "snapcraft", "application", 1)

        metrics = await _metrics(test_db_session)

        assert [p["name"] for p in metrics["application_projects"]] == [
            "snapcraft",
            "rockcraft",
        ]

    async def test_untriaged_percentage_is_over_issues_plus_prs_rounded_to_one_dp(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session)
        for n in range(3):
            test_db_session.add(
                make_issue(
                    project_id=project.id,
                    external_id=f"i{n}",
                    created_at=NOW - timedelta(days=5),
                )
            )
        for n in range(4):
            test_db_session.add(
                make_issue(
                    project_id=project.id,
                    external_id=f"p{n}",
                    issue_type="pull_request",
                    created_at=NOW - timedelta(days=5),
                )
            )

        row = (await _metrics(test_db_session))["application_projects"][0]

        assert row["open_issues"] == 3
        assert row["open_prs"] == 4
        assert row["untriaged_count"] == 7
        assert row["untriaged_pct"] == 100.0
        assert row["triage_badge_color"] == "red"

    async def test_project_with_no_open_items_reports_zero_pct_and_neutral_badge(
        self, test_db_session: AsyncSession
    ) -> None:
        await _add_project(test_db_session)

        row = (await _metrics(test_db_session))["application_projects"][0]

        assert row["untriaged_pct"] == 0.0
        assert row["triage_badge_color"] == "neutral"
        assert row["latest_release_version"] is None
        assert row["latest_release_days_ago"] is None
        assert row["release_badge_color"] == "neutral"

    async def test_show_prs_and_show_release_come_from_the_hide_lists(
        self, test_db_session: AsyncSession
    ) -> None:
        await _add_project(test_db_session, "snapcraft", "application", 1)
        config = DashboardConfig(hide_prs=["snapcraft"], hide_releases=["snapcraft"])

        row = (await _metrics(test_db_session, config))["application_projects"][0]

        assert row["show_prs"] is False
        assert row["show_release"] is False

    async def test_hidden_prs_still_contribute_to_the_untriaged_percentage(
        self, test_db_session: AsyncSession
    ) -> None:
        # NOTE: looks suspect -- `show_prs=False` hides PRs from the UI but
        # they still inflate untriaged_count/untriaged_pct for that project.
        project = await _add_project(test_db_session, "snapcraft", "application", 1)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="1",
                issue_type="pull_request",
                created_at=NOW - timedelta(days=5),
            )
        )
        config = DashboardConfig(hide_prs=["snapcraft"])

        row = (await _metrics(test_db_session, config))["application_projects"][0]

        assert row["show_prs"] is False
        assert row["untriaged_count"] == 1
        assert row["untriaged_pct"] == 100.0

    async def test_github_org_falls_back_to_canonical_when_empty(
        self, test_db_session: AsyncSession
    ) -> None:
        project = Project(
            name="snapcraft",
            category="application",
            github_org="",
            display_order=1,
        )
        test_db_session.add(project)

        row = (await _metrics(test_db_session))["application_projects"][0]

        assert row["github_org"] == "canonical"

    async def test_release_badge_thresholds_are_applied_to_the_project_row(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session, "snapcraft", "application", 1)
        test_db_session.add(
            Release(
                project_id=project.id,
                version="8.0",
                released_at=NOW - timedelta(days=61),
            )
        )

        row = (await _metrics(test_db_session))["application_projects"][0]

        assert row["latest_release_days_ago"] == 61
        assert row["release_badge_color"] == "red"

    async def test_aggregate_projects_never_appear_in_any_health_bucket(
        self, test_db_session: AsyncSession
    ) -> None:
        await _add_project(test_db_session, "all-projects", "aggregate", 1)

        metrics = await _metrics(test_db_session)

        assert metrics["application_projects"] == []
        assert metrics["library_projects"] == []
        assert metrics["other_projects"] == []


# ---------------------------------------------------------------------------
# Configured issue filtering
# ---------------------------------------------------------------------------


class TestFilteredIssues:
    """Pin how `filtered_issues` config removes rows from every metric."""

    async def test_filtered_issue_is_removed_from_volume_and_untriaged(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session, "snapcraft", "application", 1)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="999",
                created_at=NOW - timedelta(days=5),
            )
        )
        config = DashboardConfig(filtered_issues={"snapcraft": ["999"]})

        metrics = await _metrics(test_db_session, config)

        assert metrics["volume"]["open_issues"] == 0
        assert metrics["untriaged"]["issues_count"] == 0
        assert metrics["needs_triage_issues"] == []

    async def test_filter_is_scoped_to_the_named_project_only(
        self, test_db_session: AsyncSession
    ) -> None:
        snapcraft = await _add_project(test_db_session, "snapcraft", "application", 1)
        rockcraft = await _add_project(test_db_session, "rockcraft", "application", 2)
        test_db_session.add_all(
            [
                make_issue(
                    project_id=snapcraft.id,
                    external_id="999",
                    created_at=NOW - timedelta(days=5),
                ),
                make_issue(
                    project_id=rockcraft.id,
                    external_id="999",
                    created_at=NOW - timedelta(days=5),
                ),
            ]
        )
        config = DashboardConfig(filtered_issues={"snapcraft": ["999"]})

        metrics = await _metrics(test_db_session, config)

        assert metrics["volume"]["open_issues"] == 1
        assert [r["project_name"] for r in metrics["needs_triage_issues"]] == [
            "rockcraft"
        ]

    async def test_empty_filter_list_for_a_project_filters_nothing(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session, "snapcraft", "application", 1)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="999",
                created_at=NOW - timedelta(days=5),
            )
        )
        config = DashboardConfig(filtered_issues={"snapcraft": []})

        metrics = await _metrics(test_db_session, config)

        assert metrics["volume"]["open_issues"] == 1

    async def test_filtered_issues_are_not_excluded_from_project_health_rows(
        self, test_db_session: AsyncSession
    ) -> None:
        # The open-items query *does* apply the exclusion, so a filtered issue
        # drops out of the per-project row too. Pinned explicitly because the
        # exclusion is threaded through ten separate queries by hand.
        project = await _add_project(test_db_session, "snapcraft", "application", 1)
        test_db_session.add(
            make_issue(
                project_id=project.id,
                external_id="999",
                created_at=NOW - timedelta(days=5),
            )
        )
        config = DashboardConfig(filtered_issues={"snapcraft": ["999"]})

        row = (await _metrics(test_db_session, config))["application_projects"][0]

        assert row["open_issues"] == 0
        assert row["untriaged_count"] == 0

    async def test_release_queries_ignore_the_issue_filter_entirely(
        self, test_db_session: AsyncSession
    ) -> None:
        project = await _add_project(test_db_session, "snapcraft", "application", 1)
        test_db_session.add(
            Release(
                project_id=project.id,
                version="8.0",
                released_at=NOW - timedelta(days=10),
            )
        )
        config = DashboardConfig(filtered_issues={"snapcraft": ["999"]})

        metrics = await _metrics(test_db_session, config)

        assert metrics["least_recent_apps"][0]["version"] == "8.0"
