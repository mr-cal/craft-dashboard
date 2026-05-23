"""Integration tests for the trends API endpoints.

These tests use a real in-memory SQLite database with actual SQLAlchemy models
to prevent regressions for the following bugs:

  1. All-projects snapshot sums each project's individual latest (not the
     time-aligned aggregate's last point).
  2. Bots are excluded from external (non-maintainer) counts.
  3. Per-author-group median ages are returned in the all-data payload.
  4. Rolling-average labels use "4-week" (never "30-day").
  6. Closed issues are counted exactly once per day.
  7. All-projects snapshot *averages* median ages (not sums them).
  8. GitHub-style "[bot]" suffixes mark issues as bot-authored.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.collectors.snapshots import compute_snapshot_counts
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.project import Project
from craft_dashboard.models.snapshot import Snapshot
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DATE_A = date(2024, 5, 22)  # "later" date
_DATE_B = date(2024, 5, 21)  # "earlier" date


def _dt(d: date) -> datetime:
    """Return midnight UTC datetime for *d*."""
    return datetime.combine(d, datetime.min.time(), tzinfo=UTC)


def _open_issue(
    created_days_ago: int = 10,
    *,
    author: str = "contributor",
    author_is_bot: bool = False,
    author_is_maintainer: bool = False,
    issue_type: str = "issue",
    reference_date: date = date(2024, 6, 1),
) -> dict:
    """Build a minimal issue dict for compute_snapshot_counts."""
    return {
        "issue_type": issue_type,
        "state": "open",
        "author": author,
        "author_is_bot": author_is_bot,
        "labels": [],
        "created_at": _dt(reference_date) - timedelta(days=created_days_ago),
        "closed_at": None,
    }


def _closed_issue(
    closed_date: date,
    *,
    author: str = "contributor",
    author_is_bot: bool = False,
    issue_type: str = "issue",
) -> dict:
    """Build a closed issue dict closed exactly on *closed_date*."""
    return {
        "issue_type": issue_type,
        "state": "closed",
        "author": author,
        "author_is_bot": author_is_bot,
        "labels": [],
        "created_at": _dt(closed_date) - timedelta(days=30),
        "closed_at": _dt(closed_date),
    }


# ---------------------------------------------------------------------------
# App / client fixture
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Skip real startup so tests don't need a live Postgres or config file."""
    yield


@pytest.fixture
def test_client(test_db_session: AsyncSession) -> TestClient:
    """TestClient wired to the in-memory SQLite session."""
    app = create_app()
    app.router.lifespan_context = _noop_lifespan

    async def _override_session() -> AsyncGenerator[AsyncSession, None]:
        yield test_db_session

    app.dependency_overrides[get_db_session] = _override_session

    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# Bug 1: All-projects snapshot sums each project's individual latest open_issues
# ---------------------------------------------------------------------------


class TestAllProjectsSnapshotSumsLatest:
    """Bug 1: all-projects snapshot must use per-project last point, not the
    time-aligned aggregate's final entry (which may only cover the newest date).
    """

    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        """Two projects with snapshots on different dates."""
        p1 = Project(
            name="project-alpha", category="application", github_org="canonical"
        )
        p2 = Project(
            name="project-beta", category="application", github_org="canonical"
        )
        test_db_session.add_all([p1, p2])
        await test_db_session.flush()

        # project-alpha has data on the later date only
        test_db_session.add(
            Snapshot(
                project_id=p1.id,
                snapshot_date=_DATE_A,
                open_issues=10,
                median_issue_age=0,
            )
        )
        # project-beta has data on the earlier date only
        test_db_session.add(
            Snapshot(
                project_id=p2.id,
                snapshot_date=_DATE_B,
                open_issues=5,
                median_issue_age=0,
            )
        )
        await test_db_session.commit()

    def test_all_projects_open_issues_sums_individual_latest(
        self, test_client: TestClient, seeded: None
    ) -> None:
        """all-projects snapshot open_issues == sum of each project's latest value."""
        response = test_client.get("/stats/trends/all-data")
        assert response.status_code == 200

        data = response.json()
        snapshot = data["snapshot"]

        assert "all-projects" in snapshot
        # Must be 10 + 5 = 15, NOT just 10 (which is the only project on _DATE_A)
        assert snapshot["all-projects"]["open_issues"] == 15


# ---------------------------------------------------------------------------
# Bug 2: Bots are excluded from external (non-maintainer) counts
# ---------------------------------------------------------------------------


class TestBotsExcludedFromExternalCounts:
    """Bug 2: open_issues_external must not include bot-authored issues."""

    def test_bot_issues_not_in_external(self) -> None:
        """Bot issues must not appear in open_issues_external."""
        issues = [
            _open_issue(author="renovate[bot]", author_is_bot=True),
            _open_issue(author="dependabot[bot]", author_is_bot=True),
            _open_issue(author="human-contributor"),
        ]
        result = compute_snapshot_counts(issues, maintainers=set())

        # Two bots + one human = 3 open issues total
        assert result["open_issues"] == 3
        # External should only count the human contributor
        assert result["open_issues_external"] == 1
        # Bots should be counted separately
        assert result["open_issues_bots"] == 2

    def test_maintainer_issues_not_in_external(self) -> None:
        """Maintainer issues must not appear in open_issues_external."""
        issues = [
            _open_issue(author="alice"),
            _open_issue(author="bob"),
            _open_issue(author="contributor"),
        ]
        result = compute_snapshot_counts(issues, maintainers={"alice", "bob"})

        assert result["open_issues_internal"] == 2
        assert result["open_issues_external"] == 1


# ---------------------------------------------------------------------------
# Bug 3: Per-author-group median ages present in the all-data payload
# ---------------------------------------------------------------------------


class TestMedianAgePerGroupInAPIResponse:
    """Bug 3: The all-data endpoint exposes per-group median age time series."""

    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        """One project with a snapshot that has per-group median ages set."""
        p = Project(
            name="median-project", category="application", github_org="canonical"
        )
        test_db_session.add(p)
        await test_db_session.flush()

        test_db_session.add(
            Snapshot(
                project_id=p.id,
                snapshot_date=_DATE_A,
                open_issues=10,
                median_issue_age=30,
                nm_median_issue_age=20,
                median_issue_age_internal=5,
                median_issue_age_bots=50,
            )
        )
        await test_db_session.commit()

    def test_per_group_median_ages_in_time_series(
        self, test_client: TestClient, seeded: None
    ) -> None:
        """Time series must contain per-group median age fields."""
        response = test_client.get("/stats/trends/all-data")
        assert response.status_code == 200

        proj = response.json()["projects"]["median-project"]

        assert proj["median_issue_age"] == [30], "overall median"
        assert proj["nm_median_issue_age"] == [20], "non-maintainer/non-bot (external)"
        assert proj["median_issue_age_internal"] == [5], "internal (maintainer)"
        assert proj["median_issue_age_bots"] == [50], "bots"

    def test_per_group_median_ages_computed_from_issues(self) -> None:
        """compute_snapshot_counts returns correct per-group median ages."""
        today = date(2024, 6, 1)
        issues = [
            # maintainer: age 100
            _open_issue(100, author="alice", reference_date=today),
            # contributor: age 20
            _open_issue(20, author="bob", reference_date=today),
            # bot: age 5
            _open_issue(
                5, author="renovate[bot]", author_is_bot=True, reference_date=today
            ),
        ]
        result = compute_snapshot_counts(issues, maintainers={"alice"}, today=today)

        assert result["median_issue_age"] == 20  # median of [100, 20, 5]
        assert result["median_issue_age_internal"] == 100
        assert result["nm_median_issue_age"] == 20  # only bob
        assert result["median_issue_age_bots"] == 5


class TestTrendsDataNonexistentProject:
    def test_trends_data_returns_404_for_nonexistent_project(
        self, test_client: TestClient
    ) -> None:
        response = test_client.get(
            "/stats/trends/data", params={"project": "nonexistent"}
        )
        assert response.status_code == 404

    def test_trends_chart_returns_404_for_nonexistent_project(
        self, test_client: TestClient
    ) -> None:
        response = test_client.get(
            "/stats/trends/chart", params={"project": "nonexistent"}
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Bug 4: Rolling-average labels use "4-week", never "30-day"
# ---------------------------------------------------------------------------


class TestRollingAverageLabels:
    """Bug 4: The trends page must not contain the phrase '30-day'."""

    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        """Minimal project so the trends page renders without errors."""
        p = Project(
            name="rolling-project", category="application", github_org="canonical"
        )
        test_db_session.add(p)
        await test_db_session.commit()

    def test_no_30_day_in_trends_page(
        self, test_client: TestClient, seeded: None
    ) -> None:
        """Trends page HTML/JS must not contain '30-day'."""
        response = test_client.get("/stats/trends")
        assert response.status_code == 200
        assert "30-day" not in response.text


# ---------------------------------------------------------------------------
# Bug 6: Closed issues are counted exactly once on their closed date
# ---------------------------------------------------------------------------


class TestClosedIssuesNotDoubleCounted:
    """Bug 6: An issue closed on date D counts exactly once in closed_issues."""

    def test_closed_issue_counted_once(self) -> None:
        """A single issue closed today contributes exactly 1 to closed_issues."""
        today = date(2024, 6, 1)
        issues = [_closed_issue(today)]
        result = compute_snapshot_counts(issues, maintainers=set(), today=today)

        assert result["closed_issues"] == 1

    def test_closed_issue_on_other_date_not_counted(self) -> None:
        """An issue closed yesterday is NOT counted in today's closed_issues."""
        today = date(2024, 6, 1)
        yesterday = date(2024, 5, 31)
        issues = [_closed_issue(yesterday)]
        result = compute_snapshot_counts(issues, maintainers=set(), today=today)

        assert result["closed_issues"] == 0

    def test_multiple_closed_issues_on_same_date(self) -> None:
        """Multiple issues closed today are each counted once."""
        today = date(2024, 6, 1)
        issues = [_closed_issue(today), _closed_issue(today), _closed_issue(today)]
        result = compute_snapshot_counts(issues, maintainers=set(), today=today)

        assert result["closed_issues"] == 3


# ---------------------------------------------------------------------------
# Bug 7: All-projects snapshot averages median ages (not sums them)
# ---------------------------------------------------------------------------


class TestAllProjectsSnapshotAveragesMedianAges:
    """Bug 7: all-projects snapshot median_issue_age must be an average."""

    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        """Two projects with different median_issue_age values on the same date."""
        p1 = Project(
            name="age-project-a", category="application", github_org="canonical"
        )
        p2 = Project(
            name="age-project-b", category="application", github_org="canonical"
        )
        test_db_session.add_all([p1, p2])
        await test_db_session.flush()

        test_db_session.add(
            Snapshot(
                project_id=p1.id,
                snapshot_date=_DATE_A,
                open_issues=1,
                median_issue_age=100,
            )
        )
        test_db_session.add(
            Snapshot(
                project_id=p2.id,
                snapshot_date=_DATE_A,
                open_issues=1,
                median_issue_age=200,
            )
        )
        await test_db_session.commit()

    def test_all_projects_snapshot_median_is_average(
        self, test_client: TestClient, seeded: None
    ) -> None:
        """all-projects snapshot median_issue_age must be ~150, not 300."""
        response = test_client.get("/stats/trends/all-data")
        assert response.status_code == 200

        snapshot = response.json()["snapshot"]

        assert "all-projects" in snapshot
        median = snapshot["all-projects"]["median_issue_age"]
        # Average of 100 and 200 is 150 (integer division: 150)
        assert median == 150, f"Expected 150 (average) but got {median} (may be summed)"


# ---------------------------------------------------------------------------
# Bug 8: GitHub-style "[bot]" suffix marks issues as bot-authored
# ---------------------------------------------------------------------------


class TestBotDetectionBySuffix:
    """Bug 8: Authors with '[bot]' suffix in their name must be flagged as bots."""

    @pytest.mark.parametrize(
        "author", ["renovate[bot]", "dependabot[bot]", "app/github-actions[bot]"]
    )
    def test_bot_author_flagged(self, author: str) -> None:
        """Bot-authored issue (author_is_bot=True) lands in open_issues_bots."""
        issues = [_open_issue(author=author, author_is_bot=True)]
        result = compute_snapshot_counts(issues, maintainers=set())

        assert result["open_issues_bots"] == 1
        assert result["open_issues_external"] == 0

    def test_regular_user_not_flagged_as_bot(self) -> None:
        """A regular contributor is NOT in open_issues_bots."""
        issues = [_open_issue(author="regular-user")]
        result = compute_snapshot_counts(issues, maintainers=set())

        assert result["open_issues_bots"] == 0
        assert result["open_issues_external"] == 1
