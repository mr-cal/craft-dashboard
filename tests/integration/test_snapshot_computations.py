"""Integration tests for snapshot trend computations."""

from collections.abc import AsyncGenerator, Sequence
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

_TODAY = date(2024, 6, 1)


def _dt(day: date, hour: int = 0, minute: int = 0) -> datetime:
    """Return a UTC datetime on the requested day."""
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(
        hour=hour,
        minute=minute,
    )


def _open_item(
    created_at: datetime,
    *,
    issue_type: str = "issue",
    author: str = "contributor",
    author_is_bot: bool = False,
) -> dict:
    """Build a minimal open issue or PR payload."""
    return {
        "issue_type": issue_type,
        "state": "open",
        "author": author,
        "author_is_bot": author_is_bot,
        "labels": [],
        "created_at": created_at,
        "closed_at": None,
    }


def _closed_item(
    closed_at: datetime,
    *,
    created_at: datetime | None = None,
    issue_type: str = "issue",
    author: str = "contributor",
    author_is_bot: bool = False,
) -> dict:
    """Build a minimal closed issue or PR payload."""
    return {
        "issue_type": issue_type,
        "state": "merged" if issue_type == "pull_request" else "closed",
        "author": author,
        "author_is_bot": author_is_bot,
        "labels": [],
        "created_at": created_at or closed_at - timedelta(days=30),
        "closed_at": closed_at,
    }


def starcraft_get_median_date(dates: list[datetime]) -> datetime:
    """Reference implementation of median-date calculation."""
    sorted_dates = sorted(dates)
    n = len(sorted_dates)
    if n % 2 == 0:
        reference = datetime(year=2000, month=1, day=1, tzinfo=UTC)
        mid1 = sorted_dates[n // 2 - 1]
        mid2 = sorted_dates[n // 2]
        return reference + sum((d - reference for d in [mid1, mid2]), timedelta()) / 2
    return sorted_dates[n // 2]


def starcraft_get_median_age(
    dates: list[datetime], reference_date: datetime
) -> int | None:
    """Reference implementation of median-age calculation."""
    if dates:
        median_date = starcraft_get_median_date(dates)
        return (reference_date - median_date).days
    return None


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Skip real startup for tests."""
    yield


@pytest.fixture
def test_client(test_db_session: AsyncSession) -> AsyncGenerator[TestClient, None]:
    """Test client wired to the in-memory SQLite session."""
    app = create_app()
    app.router.lifespan_context = _noop_lifespan

    async def _override_session() -> AsyncGenerator[AsyncSession, None]:
        yield test_db_session

    app.dependency_overrides[get_db_session] = _override_session

    with TestClient(app) as client:
        yield client


def _open_created_at(items: Sequence[dict]) -> list[datetime]:
    """Return created_at values for open items only."""
    return [item["created_at"] for item in items if item["state"] == "open"]


class TestStarcraftStatsParity:
    @pytest.mark.parametrize(
        ("issues", "maintainers", "bots"),
        [
            pytest.param(
                [
                    _open_item(_dt(date(2024, 5, 12), 9), author="external-user"),
                    _open_item(
                        _dt(date(2024, 5, 20), 15),
                        issue_type="pull_request",
                        author="maintainer",
                    ),
                    _open_item(
                        _dt(date(2024, 5, 28), 6),
                        author="Copilot",
                        author_is_bot=True,
                    ),
                ],
                {"maintainer"},
                {"Copilot"},
                id="odd-count-mixed-items-and-author-groups",
            ),
            pytest.param(
                [
                    _open_item(_dt(date(2024, 4, 22)), author="external-user"),
                    _open_item(
                        _dt(date(2024, 5, 2)), issue_type="pull_request", author="alice"
                    ),
                    _open_item(_dt(date(2024, 5, 12)), author="Copilot"),
                    _open_item(
                        _dt(date(2024, 5, 22)), issue_type="pull_request", author="bob"
                    ),
                ],
                {"alice", "bob"},
                {"Copilot"},
                id="even-count-average-of-middle-dates",
            ),
            pytest.param(
                [
                    _open_item(_dt(date(2024, 5, 31), 23), author="external-user"),
                    _open_item(
                        _dt(date(2024, 5, 30), 1),
                        issue_type="pull_request",
                        author="maintainer",
                    ),
                ],
                {"maintainer"},
                None,
                id="even-count-non-midnight-precision",
            ),
        ],
    )
    def test_median_age_matches_reference_for_identical_items(
        self,
        issues: list[dict],
        maintainers: set[str],
        bots: set[str] | None,
    ) -> None:
        """Combined median_age must match the reference implementation for identical inputs."""
        result = compute_snapshot_counts(
            issues,
            maintainers=maintainers,
            today=_TODAY,
            bots=bots,
        )

        expected = starcraft_get_median_age(_open_created_at(issues), _dt(_TODAY))

        assert result["median_age"] == expected

    @pytest.mark.parametrize(
        "issues",
        [
            pytest.param(
                [
                    _closed_item(_dt(_TODAY, 8), author="external-user"),
                    _closed_item(
                        _dt(_TODAY, 14),
                        issue_type="pull_request",
                        author="maintainer",
                    ),
                    _closed_item(_dt(date(2024, 5, 31), 18), author="Copilot"),
                ],
                id="issues-and-prs-closed-today-only",
            ),
            pytest.param(
                [
                    _closed_item(_dt(_TODAY, 7), author="external-user"),
                    _closed_item(_dt(_TODAY, 9), author="Copilot", author_is_bot=True),
                    _closed_item(
                        _dt(_TODAY, 16),
                        issue_type="pull_request",
                        author="maintainer",
                    ),
                    _closed_item(
                        _dt(date(2024, 5, 30), 12),
                        issue_type="pull_request",
                        author="other-user",
                    ),
                ],
                id="mixed-author-groups-and-dates",
            ),
        ],
    )
    def test_closed_count_matches_reference(
        self,
        issues: list[dict],
    ) -> None:
        """Combined closed count must match the reference implementation for identical inputs."""
        result = compute_snapshot_counts(
            issues,
            maintainers={"maintainer"},
            today=_TODAY,
            bots={"Copilot"},
        )

        expected = sum(1 for issue in issues if issue["closed_at"].date() == _TODAY)

        assert result["closed_issues"] + result["closed_prs"] == expected

    def test_bot_detection_with_config_bots_list(self) -> None:
        """Configured bot usernames must be counted only in bot-specific fields."""
        issues = [
            _open_item(
                _dt(date(2024, 5, 25)), issue_type="pull_request", author="Copilot"
            ),
            _open_item(_dt(date(2024, 5, 29)), author="Copilot"),
            _open_item(_dt(date(2024, 5, 22)), author="external-user"),
            _open_item(
                _dt(date(2024, 5, 20)),
                issue_type="pull_request",
                author="maintainer",
            ),
            _closed_item(_dt(_TODAY, 11), author="Copilot"),
        ]

        result = compute_snapshot_counts(
            issues,
            maintainers={"maintainer"},
            today=_TODAY,
            bots={"Copilot"},
        )

        assert result["open_issues_bots"] + result["open_prs_bots"] == 2
        assert result["median_age_bots"] == 5
        assert result["closed_issues_bots"] + result["closed_prs_bots"] == 1
        assert result["open_issues_external"] + result["open_prs_external"] == 1
        assert result["open_issues_internal"] + result["open_prs_internal"] == 1
        assert result["open_issues_external"] == 1
        assert result["open_prs_external"] == 0
        assert result["open_issues_internal"] == 0
        assert result["open_prs_internal"] == 1

    @pytest.fixture
    async def seeded_api_data(self, test_db_session: AsyncSession) -> dict:
        """Seed one project with snapshots computed from known issue inputs."""
        maintainers = {"maintainer"}
        bots = {"Copilot"}
        project = Project(
            name="parity-project",
            category="application",
            github_org="canonical",
            display_order=1,
        )
        test_db_session.add(project)
        await test_db_session.flush()

        scenarios = [
            (
                date(2024, 5, 31),
                [
                    _open_item(_dt(date(2024, 5, 20), 9), author="external-user"),
                    _open_item(
                        _dt(date(2024, 5, 10), 10),
                        issue_type="pull_request",
                        author="maintainer",
                    ),
                    _open_item(_dt(date(2024, 5, 29), 23), author="Copilot"),
                    _closed_item(
                        _dt(date(2024, 5, 31), 12),
                        issue_type="pull_request",
                        author="Copilot",
                    ),
                    _closed_item(_dt(date(2024, 5, 31), 18), author="external-user"),
                ],
            ),
            (
                _TODAY,
                [
                    _open_item(_dt(date(2024, 5, 31), 23), author="external-user"),
                    _open_item(
                        _dt(date(2024, 5, 30), 1),
                        issue_type="pull_request",
                        author="maintainer",
                    ),
                    _open_item(_dt(date(2024, 5, 28)), author="Copilot"),
                    _closed_item(_dt(_TODAY, 7), author="Copilot"),
                    _closed_item(
                        _dt(_TODAY, 16),
                        issue_type="pull_request",
                        author="external-user",
                    ),
                ],
            ),
        ]

        expected: list[dict] = []
        for snapshot_date, issues in scenarios:
            counts = compute_snapshot_counts(
                issues,
                maintainers=maintainers,
                today=snapshot_date,
                bots=bots,
            )
            expected.append(
                {
                    "date": snapshot_date.isoformat(),
                    "counts": counts,
                    "median_age": starcraft_get_median_age(
                        _open_created_at(issues), _dt(snapshot_date)
                    ),
                }
            )
            test_db_session.add(
                Snapshot(
                    project_id=project.id,
                    snapshot_date=snapshot_date,
                    **counts,
                )
            )

        await test_db_session.commit()

        return {"project_name": project.name, "expected": expected}

    def test_end_to_end_api_combined_fields(
        self,
        test_client: TestClient,
        seeded_api_data: dict,
    ) -> None:
        """The all-data API must expose combined issue/PR trend fields correctly."""
        response = test_client.get("/stats/trends/all-data")

        assert response.status_code == 200
        payload = response.json()

        project = payload["projects"][seeded_api_data["project_name"]]
        all_projects = payload["projects"]["all-projects"]
        expected = seeded_api_data["expected"]

        assert project["dates"] == [point["date"] for point in expected]
        assert project["open"] == [
            point["counts"]["open_issues"] + point["counts"]["open_prs"]
            for point in expected
        ]
        assert project["closed"] == [
            point["counts"]["closed_issues"] + point["counts"]["closed_prs"]
            for point in expected
        ]
        assert project["open_external"] == [
            point["counts"]["open_issues_external"]
            + point["counts"]["open_prs_external"]
            for point in expected
        ]
        assert project["open_internal"] == [
            point["counts"]["open_issues_internal"]
            + point["counts"]["open_prs_internal"]
            for point in expected
        ]
        assert project["open_bots"] == [
            point["counts"]["open_issues_bots"] + point["counts"]["open_prs_bots"]
            for point in expected
        ]
        assert project["closed_external"] == [
            point["counts"]["closed_issues_external"]
            + point["counts"]["closed_prs_external"]
            for point in expected
        ]
        assert project["closed_internal"] == [
            point["counts"]["closed_issues_internal"]
            + point["counts"]["closed_prs_internal"]
            for point in expected
        ]
        assert project["closed_bots"] == [
            point["counts"]["closed_issues_bots"] + point["counts"]["closed_prs_bots"]
            for point in expected
        ]
        assert project["median_age"] == [point["median_age"] for point in expected]
        assert project["nm_median_age"] == [
            point["counts"]["nm_median_age"] for point in expected
        ]
        assert project["median_age_internal"] == [
            point["counts"]["median_age_internal"] for point in expected
        ]
        assert project["median_age_bots"] == [
            point["counts"]["median_age_bots"] for point in expected
        ]
        assert all_projects == project

    @pytest.mark.parametrize(
        "issues",
        [
            pytest.param(
                [_open_item(_dt(date(2024, 5, 20), 6), issue_type="issue")],
                id="single-item",
            ),
            pytest.param(
                [
                    _open_item(_dt(date(2024, 5, 31), 23), issue_type="issue"),
                    _open_item(
                        _dt(date(2024, 5, 30), 1),
                        issue_type="pull_request",
                    ),
                ],
                id="two-items-average-of-middle-dates",
            ),
            pytest.param(
                [
                    _open_item(_dt(date(2024, 5, 2)), issue_type="issue"),
                    _open_item(_dt(date(2024, 5, 12)), issue_type="pull_request"),
                    _open_item(_dt(date(2024, 5, 22)), issue_type="issue"),
                ],
                id="three-items-middle-value",
            ),
            pytest.param(
                [
                    _open_item(_dt(date(2024, 5, 1), 23), issue_type="issue"),
                    _open_item(_dt(date(2024, 5, 5), 12), issue_type="pull_request"),
                    _open_item(_dt(date(2024, 5, 10), 6), issue_type="issue"),
                    _open_item(_dt(date(2024, 5, 20), 18), issue_type="pull_request"),
                    _open_item(_dt(date(2024, 5, 25), 3), issue_type="issue"),
                    _open_item(_dt(date(2024, 5, 31), 23), issue_type="pull_request"),
                ],
                id="many-items-varied-ages",
            ),
        ],
    )
    def test_median_age_computation_precision(self, issues: list[dict]) -> None:
        """Median age precision must match the reference implementation across edge cases."""
        result = compute_snapshot_counts(issues, maintainers=set(), today=_TODAY)

        expected = starcraft_get_median_age(_open_created_at(issues), _dt(_TODAY))

        assert result["median_age"] == expected
