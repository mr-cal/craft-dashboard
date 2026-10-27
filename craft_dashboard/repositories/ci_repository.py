"""CI failure catalog queries.

Branch class (``main``/``pr``/``other``) is derived from ``event`` +
``head_branch`` at query time against each project's configured
``trunk-branches`` rather than stored on the row -- see
``plans/44-ci-failure-catalog.md``, Architecture point 3. That per-project
matching can't be pushed into a single SQL expression (each project has its
own glob list), so this repository fetches the raw run/failure rows and
aggregates in Python. The CI tables are bounded by the 60-day detail
retention window (see ``services/ci_gc.py``), so this stays a modest amount
of data per query, unlike the issue tables.
"""

from __future__ import annotations

import fnmatch
import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, NamedTuple

from sqlalchemy import select

from craft_dashboard.models.ci_failure import (
    CIJobFailure,
    CITestFailure,
    CIWorkflowRun,
)
from craft_dashboard.models.project import Project
from craft_dashboard.models.views import CIFailureRow, CIFilters, CIQueryResult

if TYPE_CHECKING:
    from sqlalchemy.engine import Row
    from sqlalchemy.ext.asyncio import AsyncSession

_VALID_SORT_FIELDS = {
    "fail_rate",
    "occurrence_count",
    "flip_flop_count",
    "last_seen_at",
    "signature",
}

# ci_job_failures/ci_test_failures are pruned past 60 days by ci_gc.py, so a
# run older than that has no detail left to roll up -- bounding the run scan
# to the same window keeps this query proportional to live data rather than
# "every run ever recorded" (ci_workflow_runs itself is kept for a year).
_CATALOG_LOOKBACK_DAYS = 60
# Defensive cap in case the lookback window above is ever widened without a
# corresponding review of this query's cost -- every query that can return a
# full table needs a hard limit, not just a soft time filter.
_MAX_RUNS_SCANNED = 100_000

# datetime.min has no tzinfo; comparing it against the timezone-aware
# last_seen_at values elsewhere would raise, so sorting treats a missing
# timestamp as "very old" without mixing naive/aware datetimes.
_NEVER_SEEN = datetime.min.replace(tzinfo=None)


def branch_class_for(
    event: str, head_branch: str | None, trunk_branches: list[str]
) -> str:
    """Classify a run's branch as main/pr/other, mirroring the plan's rule.

    ``merge_group`` (merge-queue) runs and pushes to a configured trunk
    branch are "main"; pull_request(_target) runs not matching a trunk
    branch are "pr"; everything else (schedule, workflow_dispatch, a push to
    some other branch) is "other".
    """
    if event == "merge_group":
        return "main"
    matches_trunk = bool(head_branch) and any(
        fnmatch.fnmatch(head_branch, pattern) for pattern in trunk_branches
    )
    if event == "push":
        return "main" if matches_trunk else "other"
    if event in ("pull_request", "pull_request_target"):
        return "main" if matches_trunk else "pr"
    return "other"


class _RunKey(NamedTuple):
    project_id: int
    run_id: int


class _LatestRun(NamedTuple):
    """The latest attempt of one (project_id, run_id), plus flip status.

    ``flipped`` is True when an earlier attempt's conclusion disagreed with
    this (the latest) attempt's conclusion -- a rerun that changed the
    outcome, per the plan's flip_flop_count definition.
    """

    workflow_run_id: int
    workflow_name: str
    conclusion: str | None
    branch_class: str
    created_at: datetime | None
    flipped: bool


class CIRepository:
    """Queries backing the CI failure catalog and signature drill-down."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_project_names(self) -> list[str]:
        """Return project names with any recorded CI runs, display-ordered."""
        rows = await self.session.execute(
            select(Project.name, Project.display_order)
            .join(CIWorkflowRun, CIWorkflowRun.project_id == Project.id)
            .distinct()
            .order_by(Project.display_order, Project.name)
        )
        return [row.name for row in rows]

    async def get_workflow_names(self, project_name: str) -> list[str]:
        """Return distinct workflow names recorded for one project."""
        rows = await self.session.execute(
            select(CIWorkflowRun.workflow_name)
            .join(Project, CIWorkflowRun.project_id == Project.id)
            .where(Project.name == project_name)
            .distinct()
            .order_by(CIWorkflowRun.workflow_name)
        )
        return [row.workflow_name for row in rows]

    async def _latest_runs(
        self, trunk_branches_by_project: dict[str, list[str]]
    ) -> dict[_RunKey, _LatestRun]:
        """Resolve each run_id to its latest attempt (plus flip-flop flag).

        A rerun keeps the same ``run_id`` but bumps ``run_attempt``; only the
        latest attempt's outcome counts towards occurrence/total counts (a
        rerun that fixed the failure shouldn't still count as "failing"),
        matching the plan's "fail rate excludes reruns' superseded attempts".
        """
        cutoff = datetime.now(tz=UTC) - timedelta(days=_CATALOG_LOOKBACK_DAYS)
        rows = await self.session.execute(
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
            .where(CIWorkflowRun.started_at >= cutoff)
            .limit(_MAX_RUNS_SCANNED)
        )

        attempts: dict[_RunKey, list[Any]] = defaultdict(list)
        for row in rows:
            attempts[_RunKey(row.project_id, row.run_id)].append(row)

        latest_by_key: dict[_RunKey, _LatestRun] = {}
        for key, run_attempts in attempts.items():
            run_attempts.sort(key=lambda r: r.run_attempt)
            latest = run_attempts[-1]
            # "Flipped" means some consecutive pair of attempts disagreed on
            # outcome -- not just that the latest differs from *an* earlier
            # one, since a fail -> pass -> fail sequence is still two flips
            # even though the latest attempt matches the very first.
            flipped = any(
                run_attempts[i].conclusion != run_attempts[i - 1].conclusion
                for i in range(1, len(run_attempts))
            )
            trunk_branches = trunk_branches_by_project.get(latest.project_name, [])
            branch_class = branch_class_for(
                latest.event, latest.head_branch, trunk_branches
            )
            latest_by_key[key] = _LatestRun(
                workflow_run_id=latest.id,
                workflow_name=latest.workflow_name,
                conclusion=latest.conclusion,
                branch_class=branch_class,
                created_at=latest.started_at,
                flipped=flipped,
            )
        return latest_by_key

    async def search(
        self,
        filters: CIFilters,
        *,
        trunk_branches_by_project: dict[str, list[str]],
    ) -> CIQueryResult:
        """Return the filtered, sorted, paginated CI failure catalog."""
        latest_runs = await self._latest_runs(trunk_branches_by_project)

        # Denominator: total latest-attempt runs per (project, workflow,
        # branch_class), regardless of outcome.
        total_counts: dict[tuple[int, str, str], int] = defaultdict(int)
        latest_by_run_id: dict[int, _LatestRun] = {}
        for key, latest in latest_runs.items():
            total_counts[
                (key.project_id, latest.workflow_name, latest.branch_class)
            ] += 1
            latest_by_run_id[latest.workflow_run_id] = latest

        failed_run_ids = {
            run_id
            for run_id, latest in latest_by_run_id.items()
            if latest.conclusion == "failure"
        }
        flipped_run_ids = {
            run_id for run_id, latest in latest_by_run_id.items() if latest.flipped
        }

        if not failed_run_ids:
            return CIQueryResult(rows=[], total_count=0, total_pages=0, page=1)

        job_query = (
            select(
                CIJobFailure.id.label("job_failure_id"),
                CIJobFailure.workflow_run_id,
                CIJobFailure.project_id,
                CIJobFailure.job_name,
                CIJobFailure.job_variant,
                CIJobFailure.created_at,
                Project.name.label("project_name"),
            )
            .join(Project, CIJobFailure.project_id == Project.id)
            .where(CIJobFailure.workflow_run_id.in_(failed_run_ids))
        )
        if filters.project:
            job_query = job_query.where(Project.name == filters.project)
        job_rows = {
            row.job_failure_id: row for row in await self.session.execute(job_query)
        }
        if not job_rows:
            return CIQueryResult(rows=[], total_count=0, total_pages=0, page=1)

        test_rows = list(
            await self.session.execute(
                select(
                    CITestFailure.job_failure_id,
                    CITestFailure.test_type,
                    CITestFailure.signature,
                    CITestFailure.error_message,
                    CITestFailure.is_environmental,
                ).where(CITestFailure.job_failure_id.in_(job_rows.keys()))
            )
        )

        # Aggregation key: (project, workflow, job, variant, test_type,
        # signature). Jobs with no parsed CITestFailure roll up at the job
        # level instead (signature == job_name), per the plan's "Also roll
        # up at the Job level ... where test-level parsing didn't produce a
        # CITestFailure".
        groups: dict[tuple, dict[str, Any]] = {}
        job_has_test_failure: set[int] = set()
        for test_row in test_rows:
            job = job_rows.get(test_row.job_failure_id)
            if job is None:
                continue
            job_has_test_failure.add(test_row.job_failure_id)
            self._accumulate(
                groups,
                job=job,
                test_type=test_row.test_type,
                signature=test_row.signature,
                error_message=test_row.error_message,
                is_environmental=test_row.is_environmental,
                latest_by_run_id=latest_by_run_id,
            )

        for job_failure_id, job in job_rows.items():
            if job_failure_id in job_has_test_failure:
                continue
            self._accumulate(
                groups,
                job=job,
                test_type="job",
                signature=job.job_name,
                error_message=None,
                is_environmental=False,
                latest_by_run_id=latest_by_run_id,
            )

        rows = []
        for key, agg in groups.items():
            project_id, workflow_name, job_name, job_variant, test_type, signature = key
            branch_class = agg["branch_class"]
            if filters.branch_class and branch_class != filters.branch_class:
                continue
            if filters.workflow and workflow_name != filters.workflow:
                continue
            if filters.test_type and test_type != filters.test_type:
                continue
            if filters.search and filters.search.lower() not in signature.lower():
                continue
            total_count = total_counts.get((project_id, workflow_name, branch_class), 0)
            rows.append(
                CIFailureRow(
                    project_name=agg["project_name"],
                    workflow_name=workflow_name,
                    job_name=job_name,
                    job_variant=job_variant,
                    test_type=test_type,
                    signature=signature,
                    branch_class=branch_class,
                    occurrence_count=len(agg["run_ids"]),
                    total_count=total_count,
                    flip_flop_count=len(agg["run_ids"] & flipped_run_ids),
                    is_environmental=agg["is_environmental"],
                    last_seen_at=agg["last_seen_at"],
                    sample_error_message=agg["sample_error_message"],
                )
            )

        rows = _sort_rows(rows, filters.sort_by)

        total_count = len(rows)
        page = max(1, filters.page)
        if filters.items_per_page:
            total_pages = math.ceil(total_count / filters.items_per_page)
            start = (page - 1) * filters.items_per_page
            rows = rows[start : start + filters.items_per_page]
        else:
            total_pages = 1 if total_count else 0

        return CIQueryResult(
            rows=rows, total_count=total_count, total_pages=total_pages, page=page
        )

    def _accumulate(
        self,
        groups: dict[tuple, dict[str, Any]],
        *,
        job: Row[Any],
        test_type: str,
        signature: str,
        error_message: str | None,
        is_environmental: bool,
        latest_by_run_id: dict[int, _LatestRun],
    ) -> None:
        """Fold one (job, signature) occurrence into its aggregate group.

        ``job.workflow_run_id`` is guaranteed to already be a latest-attempt
        ID (the job query only selected failures for IDs in
        ``failed_run_ids``, itself built only from latest attempts), so no
        extra "is this superseded" check is needed here.
        """
        latest = latest_by_run_id[job.workflow_run_id]
        key = (
            job.project_id,
            latest.workflow_name,
            job.job_name,
            job.job_variant,
            test_type,
            signature,
        )
        agg = groups.setdefault(
            key,
            {
                "project_name": job.project_name,
                "branch_class": latest.branch_class,
                "run_ids": set(),
                "is_environmental": False,
                "last_seen_at": None,
                "sample_error_message": None,
            },
        )
        agg["run_ids"].add(job.workflow_run_id)
        agg["is_environmental"] = agg["is_environmental"] or is_environmental
        if agg["last_seen_at"] is None or (
            job.created_at and job.created_at > agg["last_seen_at"]
        ):
            agg["last_seen_at"] = job.created_at
        if agg["sample_error_message"] is None and error_message:
            agg["sample_error_message"] = error_message


def _sort_rows(rows: list[CIFailureRow], sort_by: str) -> list[CIFailureRow]:
    """Sort catalog rows, default fail_rate desc then occurrence_count desc."""
    descending = not sort_by.startswith("-")
    field = sort_by.lstrip("-") or "fail_rate"
    if field not in _VALID_SORT_FIELDS:
        field, descending = "fail_rate", True

    def sort_key(row: CIFailureRow) -> tuple[float, int] | int | datetime | str:
        if field == "fail_rate":
            rate = row.fail_rate
            return (rate if rate is not None else -1.0, row.occurrence_count)
        if field == "occurrence_count":
            return row.occurrence_count
        if field == "flip_flop_count":
            return row.flip_flop_count
        if field == "last_seen_at":
            last_seen = row.last_seen_at
            return last_seen.replace(tzinfo=None) if last_seen else _NEVER_SEEN
        return row.signature.lower()

    return sorted(rows, key=sort_key, reverse=descending)
