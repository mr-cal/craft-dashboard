"""Aggregation service composing CI repository queries into view models.

Keeps ``routes/ci.py`` free of any SQL or branch-class logic -- it only
calls into this service and renders what comes back, matching the
routes -> services -> repositories layering used across the app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from craft_dashboard.models.ci_failure import (
    CIFailureDailyCount,
    CIJobFailure,
    CITestFailure,
    CIWorkflowRun,
)
from craft_dashboard.models.project import Project
from craft_dashboard.models.views import (
    CIDailyPoint,
    CIFilters,
    CIQueryResult,
    CIRunOccurrence,
    CISignatureDetail,
)
from craft_dashboard.repositories.ci_repository import CIRepository, branch_class_for

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from craft_dashboard.config import DashboardConfig

# How many individual linked runs to show in a signature's drill-down list.
# The detail window itself (60 days) is enforced by GC pruning, not here.
MAX_RECENT_RUNS = 50


def _trunk_branches_by_project(
    config: DashboardConfig, project_names: list[str]
) -> dict[str, list[str]]:
    return {name: config.ci.trunk_branches_for(name) for name in project_names}


class CIService:
    """Composes CI repository queries into catalog and drill-down views."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = CIRepository(session)

    async def get_project_names(self) -> list[str]:
        """Return project names with any recorded CI runs."""
        return await self.repo.get_project_names()

    async def get_workflow_names(self, project_name: str) -> list[str]:
        """Return workflow names recorded for one project."""
        if not project_name:
            return []
        return await self.repo.get_workflow_names(project_name)

    async def get_catalog(
        self, filters: CIFilters, config: DashboardConfig
    ) -> CIQueryResult:
        """Return the filtered, sorted, paginated CI failure catalog."""
        project_names = await self.repo.get_project_names()
        trunk_branches = _trunk_branches_by_project(config, project_names)
        return await self.repo.search(filters, trunk_branches_by_project=trunk_branches)

    async def get_signature_detail(
        self,
        *,
        project_name: str,
        workflow_name: str,
        job_name: str,
        job_variant: str | None,
        test_type: str,
        signature: str,
        branch_class: str,
        config: DashboardConfig,
    ) -> CISignatureDetail:
        """Return the drill-down payload for one signature.

        ``branch_class`` scopes both the trend chart and the recent-runs
        list ("" means all branch classes blended), mirroring the catalog's
        own filter so the two views never disagree about what's included.
        """
        daily_trend = await self._daily_trend(
            project_name=project_name, signature=signature, branch_class=branch_class
        )
        recent_runs = await self._recent_runs(
            project_name=project_name,
            workflow_name=workflow_name,
            job_name=job_name,
            job_variant=job_variant,
            test_type=test_type,
            signature=signature,
            branch_class=branch_class,
            config=config,
        )
        return CISignatureDetail(
            signature=signature,
            test_type=test_type,
            job_name=job_name,
            job_variant=job_variant,
            is_environmental=await self._is_environmental(
                project_name=project_name, signature=signature
            ),
            daily_trend=daily_trend,
            recent_runs=recent_runs,
        )

    async def _is_environmental(self, *, project_name: str, signature: str) -> bool:
        result = await self.session.execute(
            select(CITestFailure.is_environmental)
            .join(CIJobFailure, CITestFailure.job_failure_id == CIJobFailure.id)
            .join(Project, CIJobFailure.project_id == Project.id)
            .where(Project.name == project_name)
            .where(CITestFailure.signature == signature)
            .where(CITestFailure.is_environmental.is_(True))
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _daily_trend(
        self, *, project_name: str, signature: str, branch_class: str
    ) -> list[CIDailyPoint]:
        query = (
            select(
                CIFailureDailyCount.day,
                CIFailureDailyCount.fail_count,
                CIFailureDailyCount.total_count,
            )
            .join(Project, CIFailureDailyCount.project_id == Project.id)
            .where(Project.name == project_name)
            .where(CIFailureDailyCount.signature == signature)
        )
        if branch_class:
            query = query.where(CIFailureDailyCount.branch_class == branch_class)
        query = query.order_by(CIFailureDailyCount.day)
        rows = await self.session.execute(query)

        if not branch_class:
            # Blend branch classes for the same day into one point.
            by_day: dict[date, list[int]] = {}
            for row in rows:
                fail, total = by_day.setdefault(row.day, [0, 0])
                by_day[row.day] = [fail + row.fail_count, total + row.total_count]
            return [
                CIDailyPoint(day=day, fail_count=counts[0], total_count=counts[1])
                for day, counts in sorted(by_day.items())
            ]
        return [
            CIDailyPoint(
                day=row.day, fail_count=row.fail_count, total_count=row.total_count
            )
            for row in rows
        ]

    async def _recent_runs(
        self,
        *,
        project_name: str,
        workflow_name: str,
        job_name: str,
        job_variant: str | None,
        test_type: str,
        signature: str,
        branch_class: str,
        config: DashboardConfig,
    ) -> list[CIRunOccurrence]:
        query = (
            select(
                CIWorkflowRun.run_id,
                CIWorkflowRun.event,
                CIWorkflowRun.head_branch,
                CIWorkflowRun.html_url,
                CIWorkflowRun.started_at,
                CITestFailure.error_message,
            )
            .select_from(CITestFailure)
            .join(CIJobFailure, CITestFailure.job_failure_id == CIJobFailure.id)
            .join(CIWorkflowRun, CIJobFailure.workflow_run_id == CIWorkflowRun.id)
            .join(Project, CIJobFailure.project_id == Project.id)
            .where(Project.name == project_name)
            .where(CIWorkflowRun.workflow_name == workflow_name)
            .where(CIJobFailure.job_name == job_name)
            .where(CITestFailure.test_type == test_type)
            .where(CITestFailure.signature == signature)
        )
        if job_variant is not None:
            query = query.where(CIJobFailure.job_variant == job_variant)
        query = query.order_by(CIWorkflowRun.started_at.desc()).limit(MAX_RECENT_RUNS)

        trunk_branches = config.ci.trunk_branches_for(project_name)
        runs = [
            CIRunOccurrence(
                project_name=project_name,
                workflow_name=workflow_name,
                run_id=row.run_id,
                head_branch=row.head_branch,
                branch_class=branch_class_for(
                    row.event, row.head_branch, trunk_branches
                ),
                html_url=row.html_url,
                created_at=row.started_at,
                error_message=row.error_message,
            )
            for row in await self.session.execute(query)
        ]
        if branch_class:
            runs = [r for r in runs if r.branch_class == branch_class]
        return runs
