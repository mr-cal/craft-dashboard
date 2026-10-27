"""Unit tests for craft_dashboard.services.ci_gc."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from craft_dashboard.config import DashboardConfig
from craft_dashboard.models.ci_failure import (
    CIFailureDailyCount,
    CIJobFailure,
    CITestFailure,
    CIWorkflowRun,
)
from craft_dashboard.models.project import Project
from craft_dashboard.services.ci_gc import run_ci_gc
from sqlalchemy import select

_NOW = datetime.now(tz=UTC)


async def _seed_old_failure(session, *, project_id=1, run_id=1, days_old=90):
    started_at = _NOW - timedelta(days=days_old)
    run = CIWorkflowRun(
        project_id=project_id,
        run_id=run_id,
        run_attempt=1,
        workflow_name="CI",
        head_branch="main",
        head_sha="a" * 40,
        event="push",
        is_fork_pr=False,
        status="completed",
        conclusion="failure",
        started_at=started_at,
        completed_at=started_at,
        last_fetched_at=_NOW,
        html_url="https://example.test/1",
    )
    session.add(run)
    await session.flush()

    job = CIJobFailure(
        workflow_run_id=run.id,
        project_id=project_id,
        job_id=run_id * 10,
        job_name="unit-tests",
        conclusion="failure",
        created_at=started_at,
    )
    session.add(job)
    await session.flush()

    failure = CITestFailure(
        job_failure_id=job.id,
        test_type="pytest",
        signature="tests/unit/test_foo.py::test_bar",
        error_message="boom",
        is_environmental=False,
    )
    session.add(failure)
    await session.commit()
    return run, job, failure


async def test_rolls_up_and_prunes_old_detail_rows(test_db_session):
    session = test_db_session
    session.add(Project(id=1, name="snapcraft", category="application"))
    await session.flush()
    await _seed_old_failure(session, days_old=90)

    stats = await run_ci_gc(session, DashboardConfig(), now=_NOW)

    assert stats.daily_counts_written == 1
    assert stats.job_failures_pruned == 1
    assert stats.test_failures_pruned == 1

    daily_rows = (await session.execute(select(CIFailureDailyCount))).scalars().all()
    assert len(daily_rows) == 1
    assert daily_rows[0].signature == "tests/unit/test_foo.py::test_bar"
    assert daily_rows[0].fail_count == 1
    assert daily_rows[0].total_count == 1
    assert daily_rows[0].branch_class == "main"

    remaining_jobs = (await session.execute(select(CIJobFailure))).scalars().all()
    remaining_tests = (await session.execute(select(CITestFailure))).scalars().all()
    assert remaining_jobs == []
    assert remaining_tests == []


async def test_keeps_recent_detail_rows(test_db_session):
    session = test_db_session
    session.add(Project(id=1, name="snapcraft", category="application"))
    await session.flush()
    await _seed_old_failure(session, days_old=5)

    stats = await run_ci_gc(session, DashboardConfig(), now=_NOW)

    assert stats.daily_counts_written == 0
    assert stats.job_failures_pruned == 0
    assert stats.test_failures_pruned == 0

    remaining_jobs = (await session.execute(select(CIJobFailure))).scalars().all()
    assert len(remaining_jobs) == 1


async def test_prunes_workflow_runs_past_run_retention(test_db_session):
    session = test_db_session
    session.add(Project(id=1, name="snapcraft", category="application"))
    await session.flush()
    await _seed_old_failure(session, days_old=400)

    stats = await run_ci_gc(session, DashboardConfig(), now=_NOW)

    assert stats.runs_pruned == 1
    remaining_runs = (await session.execute(select(CIWorkflowRun))).scalars().all()
    assert remaining_runs == []


async def test_job_level_rollup_when_no_test_failure_parsed(test_db_session):
    session = test_db_session
    session.add(Project(id=1, name="snapcraft", category="application"))
    await session.flush()
    started_at = _NOW - timedelta(days=90)
    run = CIWorkflowRun(
        project_id=1,
        run_id=1,
        run_attempt=1,
        workflow_name="CI",
        head_branch="main",
        head_sha="a" * 40,
        event="push",
        is_fork_pr=False,
        status="completed",
        conclusion="failure",
        started_at=started_at,
        completed_at=started_at,
        last_fetched_at=_NOW,
    )
    session.add(run)
    await session.flush()
    job = CIJobFailure(
        workflow_run_id=run.id,
        project_id=1,
        job_id=1,
        job_name="build",
        conclusion="failure",
        created_at=started_at,
    )
    session.add(job)
    await session.commit()

    await run_ci_gc(session, DashboardConfig(), now=_NOW)

    daily_rows = (await session.execute(select(CIFailureDailyCount))).scalars().all()
    assert len(daily_rows) == 1
    assert daily_rows[0].signature == "build"
