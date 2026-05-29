"""Tests for collector module exports."""

from craft_dashboard.collectors import (
    dependencies,
    github,
    launchpad,
    scheduler,
    snapshots,
)


def test_github_exports() -> None:
    assert hasattr(github, "__all__")
    assert github.__all__ == ["GitHubCollector"]


def test_dependencies_exports() -> None:
    assert hasattr(dependencies, "__all__")
    assert dependencies.__all__ == [
        "DependencyCollector",
        "parse_requirements_line",
        "parse_uv_lock",
        "get_latest_for_branch",
    ]


def test_snapshots_exports() -> None:
    assert hasattr(snapshots, "__all__")
    assert snapshots.__all__ == [
        "compute_snapshot_counts",
        "generate_snapshot",
        "backfill_missing_snapshots",
    ]


def test_launchpad_exports() -> None:
    assert hasattr(launchpad, "__all__")
    assert launchpad.__all__ == ["LaunchpadCollector"]


def test_scheduler_exports() -> None:
    assert hasattr(scheduler, "__all__")
    assert scheduler.__all__ == [
        "is_due_for_refresh",
        "distribute_refresh_dates",
        "update_refresh_schedule",
        "record_refresh_error",
    ]
