"""Retention garbage collection for CI failure detail tables.

Rolls each day's occurrence/total counts into ``CIFailureDailyCount`` before
pruning ``ci_job_failures``/``ci_test_failures`` rows older than the
retention window, so long-range trend charts survive detail pruning (see
``plans/44-ci-failure-catalog.md``, Design Decisions). ``ci_workflow_runs``
itself is kept much longer since it carries no log text and is cheap.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from craft_dashboard.models.ci_failure import (
    CIFailureDailyCount,
    CIJobFailure,
    CITestFailure,
    CIWorkflowRun,
)
from craft_dashboard.models.project import Project
from craft_dashboard.repositories.ci_repository import branch_class_for

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from craft_dashboard.config import DashboardConfig

DEFAULT_DETAIL_RETENTION_DAYS = 60
DEFAULT_RUN_RETENTION_DAYS = 365


@dataclass(frozen=True)
class CIGCStats:
    """Row counts touched by one garbage-collection pass."""

    daily_counts_written: int
    job_failures_pruned: int
    test_failures_pruned: int
    runs_pruned: int


class _LatestRunInfo(NamedTuple):
    """One run's latest-attempt outcome, resolved for rollup purposes."""

    project_id: int
    project_name: str
    workflow_name: str
    branch_class: str
    day: date
    conclusion: str | None


async def run_ci_gc(
    session: AsyncSession,
    config: DashboardConfig,
    *,
    detail_retention_days: int = DEFAULT_DETAIL_RETENTION_DAYS,
    run_retention_days: int = DEFAULT_RUN_RETENTION_DAYS,
    now: datetime | None = None,
) -> CIGCStats:
    """Roll up and prune CI failure detail rows past their retention window.

    The caller owns the transaction boundary (this commits once at the end),
    matching the services/ contract.
    """
    now = now or datetime.now(tz=UTC)
    detail_cutoff = now - timedelta(days=detail_retention_days)
    run_cutoff = now - timedelta(days=run_retention_days)

    daily_counts_written = await _rollup_daily_counts(
        session, config, cutoff=detail_cutoff
    )
    job_failures_pruned, test_failures_pruned = await _prune_detail_rows(
        session, cutoff=detail_cutoff
    )
    runs_pruned = await _prune_old_runs(session, cutoff=run_cutoff)
    await session.commit()

    return CIGCStats(
        daily_counts_written=daily_counts_written,
        job_failures_pruned=job_failures_pruned,
        test_failures_pruned=test_failures_pruned,
        runs_pruned=runs_pruned,
    )


async def _resolve_latest_runs(
    session: AsyncSession, config: DashboardConfig, *, cutoff: datetime
) -> dict[int, _LatestRunInfo]:
    """Return workflow_run_id -> info for the latest attempt of every run.

    Only runs with at least one attempt at or before ``cutoff`` are
    included. Mirrors ``repositories/ci_repository.py``'s latest-attempt
    resolution: a rerun keeps the same ``run_id`` but bumps ``run_attempt``,
    and only the latest attempt's outcome should be rolled up or pruned.
    """
    rows = list(
        await session.execute(
            select(
                CIWorkflowRun.id,
                CIWorkflowRun.project_id,
                Project.name.label("project_name"),
                CIWorkflowRun.run_id,
                CIWorkflowRun.run_attempt,
                CIWorkflowRun.workflow_name,
                CIWorkflowRun.event,
                CIWorkflowRun.head_branch,
                CIWorkflowRun.conclusion,
                CIWorkflowRun.started_at,
            )
            .join(Project, CIWorkflowRun.project_id == Project.id)
            .where(CIWorkflowRun.started_at <= cutoff)
        )
    )

    attempts: dict[tuple[int, int], list[Any]] = defaultdict(list)
    for row in rows:
        attempts[(row.project_id, row.run_id)].append(row)

    latest_by_run_id: dict[int, _LatestRunInfo] = {}
    for run_attempts in attempts.values():
        run_attempts.sort(key=lambda r: r.run_attempt)
        latest = run_attempts[-1]
        if latest.started_at is None:
            continue
        trunk_branches = config.ci.trunk_branches_for(latest.project_name)
        branch_class = branch_class_for(
            latest.event, latest.head_branch, trunk_branches
        )
        latest_by_run_id[latest.id] = _LatestRunInfo(
            project_id=latest.project_id,
            project_name=latest.project_name,
            workflow_name=latest.workflow_name,
            branch_class=branch_class,
            day=latest.started_at.date(),
            conclusion=latest.conclusion,
        )
    return latest_by_run_id


async def _rollup_daily_counts(
    session: AsyncSession, config: DashboardConfig, *, cutoff: datetime
) -> int:
    """Fold every run about to age out of the detail window into daily counts."""
    latest_by_run_id = await _resolve_latest_runs(session, config, cutoff=cutoff)
    if not latest_by_run_id:
        return 0

    # Denominator: total latest-attempt runs per (project, workflow,
    # branch_class, day), regardless of outcome.
    totals: dict[tuple[int, str, str, date], int] = defaultdict(int)
    for info in latest_by_run_id.values():
        totals[(info.project_id, info.workflow_name, info.branch_class, info.day)] += 1

    failed_run_ids = {
        run_id
        for run_id, info in latest_by_run_id.items()
        if info.conclusion == "failure"
    }

    # (project_id, workflow_name, branch_class, day, signature) -> occurrence count
    fail_counts: dict[tuple[int, str, str, date, str], int] = defaultdict(int)
    if failed_run_ids:
        job_rows = list(
            await session.execute(
                select(
                    CIJobFailure.id,
                    CIJobFailure.workflow_run_id,
                    CIJobFailure.job_name,
                ).where(CIJobFailure.workflow_run_id.in_(failed_run_ids))
            )
        )
        job_by_id = {job.id: job for job in job_rows}
        test_rows = list(
            await session.execute(
                select(CITestFailure.job_failure_id, CITestFailure.signature).where(
                    CITestFailure.job_failure_id.in_(job_by_id.keys())
                )
            )
        )

        signatures_by_run: dict[int, set[str]] = defaultdict(set)
        jobs_with_test_failure: set[int] = set()
        for test_row in test_rows:
            job = job_by_id.get(test_row.job_failure_id)
            if job is None:
                continue
            jobs_with_test_failure.add(job.id)
            signatures_by_run[job.workflow_run_id].add(test_row.signature)
        # Jobs with no parsed test signature roll up at the job level,
        # mirroring the live catalog's own fallback.
        for job in job_rows:
            if job.id in jobs_with_test_failure:
                continue
            signatures_by_run[job.workflow_run_id].add(job.job_name)

        for run_id, signatures in signatures_by_run.items():
            info = latest_by_run_id.get(run_id)
            if info is None:
                continue
            for signature in signatures:
                fail_counts[
                    (
                        info.project_id,
                        info.workflow_name,
                        info.branch_class,
                        info.day,
                        signature,
                    )
                ] += 1

    written = 0
    for (
        project_id,
        workflow_name,
        branch_class,
        day,
        signature,
    ), fail_count in fail_counts.items():
        total_count = totals.get((project_id, workflow_name, branch_class, day), 0)
        await _upsert_daily_count(
            session,
            project_id=project_id,
            signature=signature,
            branch_class=branch_class,
            day=day,
            fail_count=fail_count,
            total_count=total_count,
        )
        written += 1

    return written


async def _upsert_daily_count(
    session: AsyncSession,
    *,
    project_id: int,
    signature: str,
    branch_class: str,
    day: date,
    fail_count: int,
    total_count: int,
) -> None:
    stmt = insert(CIFailureDailyCount).values(
        project_id=project_id,
        signature=signature,
        branch_class=branch_class,
        day=day,
        fail_count=fail_count,
        total_count=total_count,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "signature", "branch_class", "day"],
        set_={
            "fail_count": stmt.excluded.fail_count,
            "total_count": stmt.excluded.total_count,
        },
    )
    await session.execute(stmt)


async def _prune_detail_rows(
    session: AsyncSession, *, cutoff: datetime
) -> tuple[int, int]:
    """Delete ci_job_failures/ci_test_failures created at or before cutoff."""
    old_job_ids = select(CIJobFailure.id).where(CIJobFailure.created_at <= cutoff)
    test_result = cast(
        Any,
        await session.execute(
            delete(CITestFailure).where(CITestFailure.job_failure_id.in_(old_job_ids))
        ),
    )
    job_result = cast(
        Any,
        await session.execute(
            delete(CIJobFailure).where(CIJobFailure.created_at <= cutoff)
        ),
    )
    return job_result.rowcount or 0, test_result.rowcount or 0


async def _prune_old_runs(session: AsyncSession, *, cutoff: datetime) -> int:
    """Delete ci_workflow_runs older than the (much longer) run retention window."""
    result = cast(
        Any,
        await session.execute(
            delete(CIWorkflowRun).where(CIWorkflowRun.started_at <= cutoff)
        ),
    )
    return result.rowcount or 0
