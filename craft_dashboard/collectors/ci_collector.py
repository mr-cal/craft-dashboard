"""GitHub Actions CI failure collector.

Collects workflow runs (every outcome, not just failures -- the fail-rate
denominator needs the total run count) and, for failed runs, their failed
jobs and parsed test/tool failure signatures. See
``plans/44-ci-failure-catalog.md`` for the full design, including the
rate-limit/log-size defenses this mirrors from ``collectors/github.py``.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import github
import httpx
import sqlalchemy as sa
from github import Github
from sqlalchemy.dialects.postgresql import insert
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from craft_dashboard.collectors.ci.parsers import (
    parse_check_annotations,
    parse_log_tail,
)
from craft_dashboard.http_retry import (
    is_retriable_http_error,
    make_before_sleep_log,
    make_wait_honoring_retry_after,
)
from craft_dashboard.models.ci_failure import CIJobFailure, CITestFailure, CIWorkflowRun

if TYPE_CHECKING:
    from github.Repository import Repository as GHRepository
    from github.WorkflowJob import WorkflowJob
    from github.WorkflowRun import WorkflowRun
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["CIFailureCollector"]

logger = logging.getLogger(__name__)

# Only fetch runs created within this lookback cap, regardless of how far
# back the incremental watermark re-derivation would otherwise reach -- a
# long outage must not trigger a full-history CI backfill.
_MAX_LOOKBACK_DAYS = 14
# A completed run is still eligible to be rerun (and so still worth
# re-checking) within this many days of completing.
_RERUN_GRACE_DAYS = 7
# Tail window used when a job has no Check Run Annotations and its log must
# be downloaded instead. Lines, not bytes, bound memory use without needing
# to buffer a full (sometimes 50MB+) log.
_LOG_TAIL_LINES = 500
_MAX_HTTP_ATTEMPTS = 4

_FAILURE_CONCLUSIONS = frozenset({"failure", "timed_out", "cancelled"})
# GitHub returns this when workflow run logs have already expired.
_HTTP_GONE = 410
# The redirect target (short-lived blob storage) returns this when the log
# was evicted between the redirect being issued and the blob being fetched —
# a race, not an error worth retrying or surfacing, same as _HTTP_GONE.
_HTTP_NOT_FOUND = 404


def _parse_job_variant(job_name: str) -> tuple[str, str | None]:
    """Split a matrix job name into its base name and matrix variant.

    e.g. "integration-spread-tests (openstack:ubuntu-24.04-64)" splits into
    ``("integration-spread-tests", "openstack:ubuntu-24.04-64")`` so matrix
    cells of the same job are grouped as one job across variants.
    """
    if job_name.endswith(")") and " (" in job_name:
        base, _, variant = job_name.rpartition(" (")
        return base, variant[:-1]
    return job_name, None


class CIFailureCollector:
    """Collects GitHub Actions workflow run/job/test failure data."""

    def __init__(
        self,
        token: str,
        org: str = "canonical",
        *,
        include_fork_prs: bool = False,
    ) -> None:
        """Initialize the CI failure collector.

        Args:
            token: GitHub personal access token.
            org: GitHub organization name.
            include_fork_prs: Whether to collect runs triggered by fork PRs.
                Off by default -- fork PR runs commonly fail for
                permissions/secrets reasons unrelated to code and would
                pollute flake/persistence stats.

        """
        self.gh = Github(auth=github.Auth.Token(token), timeout=30)
        self.org = org
        self.include_fork_prs = include_fork_prs
        self._token = token
        self._http: httpx.AsyncClient | None = None

    @property
    def http(self) -> httpx.AsyncClient:
        """Return a persistent async HTTP client for job log downloads."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        return self._http

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    async def _resolve_since(self, session: AsyncSession, project_id: int) -> datetime:
        """Determine the fetch floor for this collection pass.

        Unlike issue collection, CI collection is always bounded by
        ``_MAX_LOOKBACK_DAYS`` -- there is no "full history" mode, per the
        plan's Bounded Collection Window rate-limit defense. Within that
        cap, the floor is pulled back to the oldest ``last_fetched_at``
        among runs still rerun-eligible or incomplete, so a late rerun of an
        already-completed run is never silently missed.
        """
        max_lookback = datetime.now(tz=UTC) - timedelta(days=_MAX_LOOKBACK_DAYS)
        rerun_cutoff = datetime.now(tz=UTC) - timedelta(days=_RERUN_GRACE_DAYS)
        still_eligible = sa.and_(
            CIWorkflowRun.project_id == project_id,
            sa.or_(
                CIWorkflowRun.completed_at.is_(None),
                CIWorkflowRun.completed_at >= rerun_cutoff,
            ),
        )
        oldest_fetch = await session.scalar(
            sa.select(sa.func.min(CIWorkflowRun.last_fetched_at)).where(still_eligible)
        )
        if oldest_fetch is None:
            return max_lookback
        oldest_fetch_tz = (
            oldest_fetch.replace(tzinfo=UTC)
            if oldest_fetch.tzinfo is None
            else oldest_fetch
        )
        return max(oldest_fetch_tz - timedelta(days=1), max_lookback)

    async def collect(
        self,
        project_name: str,
        project_id: int,
        session: AsyncSession,
        *,
        include_fork_prs: bool | None = None,
    ) -> int:
        """Collect recent workflow runs and failure data for one project.

        Args:
            project_name: Repository name within the configured org.
            project_id: The database ID of the project.
            session: An async SQLAlchemy session.
            include_fork_prs: Per-project override of the constructor
                default, for projects opted in via the ``[ci]`` config
                section's ``include-fork-prs``.

        Returns:
            The number of CITestFailure (or, absent a parsed signature,
            CIJobFailure) rows recorded.

        """
        since = await self._resolve_since(session, project_id)
        repo = self.gh.get_repo(f"{self.org}/{project_name}")
        failures_recorded = 0
        now = datetime.now(tz=UTC)
        allow_fork_prs = (
            self.include_fork_prs if include_fork_prs is None else include_fork_prs
        )

        for run in repo.get_workflow_runs(created=f">={since.date().isoformat()}"):
            run_created = run.created_at
            if run_created is not None and run_created.replace(tzinfo=UTC) < since:
                continue
            is_fork_pr = bool(
                run.head_repository is not None
                and getattr(run.head_repository, "fork", False)
                and run.event in ("pull_request", "pull_request_target")
            )
            if is_fork_pr and not allow_fork_prs:
                continue

            await self._upsert_run(
                session, project_id, run, is_fork_pr=is_fork_pr, fetched_at=now
            )

            if run.conclusion not in _FAILURE_CONCLUSIONS:
                continue
            failures_recorded += await self._collect_run_failures(
                session, project_id, project_name, run
            )

        await session.commit()
        return failures_recorded

    async def _upsert_run(
        self,
        session: AsyncSession,
        project_id: int,
        run: WorkflowRun,
        *,
        is_fork_pr: bool,
        fetched_at: datetime,
    ) -> None:
        """Stage a workflow run upsert on the session without committing."""
        stmt = insert(CIWorkflowRun).values(
            project_id=project_id,
            run_id=run.id,
            run_attempt=run.run_attempt or 1,
            workflow_name=run.name or "unknown",
            head_branch=run.head_branch,
            head_sha=run.head_sha,
            event=run.event,
            is_fork_pr=is_fork_pr,
            status=run.status,
            conclusion=run.conclusion,
            started_at=run.run_started_at,
            completed_at=run.updated_at if run.status == "completed" else None,
            gh_updated_at=run.updated_at,
            last_fetched_at=fetched_at,
            html_url=run.html_url,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["project_id", "run_id", "run_attempt"],
            set_={
                "status": stmt.excluded.status,
                "conclusion": stmt.excluded.conclusion,
                "completed_at": stmt.excluded.completed_at,
                "gh_updated_at": stmt.excluded.gh_updated_at,
                "last_fetched_at": stmt.excluded.last_fetched_at,
            },
        )
        await session.execute(stmt)

    async def _collect_run_failures(
        self,
        session: AsyncSession,
        project_id: int,
        project_name: str,
        run: WorkflowRun,
    ) -> int:
        """Record every failed job (and parsed signatures) for one run."""
        workflow_run_id = await session.scalar(
            sa.select(CIWorkflowRun.id).where(
                CIWorkflowRun.project_id == project_id,
                CIWorkflowRun.run_id == run.id,
                CIWorkflowRun.run_attempt == (run.run_attempt or 1),
            )
        )
        if workflow_run_id is None:
            logger.warning(
                "Could not find just-upserted CIWorkflowRun for %s run %d",
                project_name,
                run.id,
            )
            return 0

        already_recorded = set(
            await session.scalars(
                sa.select(CIJobFailure.job_id).where(
                    CIJobFailure.workflow_run_id == workflow_run_id
                )
            )
        )

        failures_recorded = 0
        for job in run.jobs():
            if job.conclusion not in _FAILURE_CONCLUSIONS:
                continue
            if job.id in already_recorded:
                # Still rerun-eligible (see _RERUN_GRACE_DAYS) and refetched
                # by every pass until it ages out; already recorded on a
                # prior pass, so re-inserting here would just duplicate it.
                continue
            job_name, job_variant = _parse_job_variant(job.name)
            failed_step = next(
                (
                    step.name
                    for step in (job.steps or [])
                    if step.conclusion in _FAILURE_CONCLUSIONS
                ),
                None,
            )
            job_failure = CIJobFailure(
                workflow_run_id=workflow_run_id,
                project_id=project_id,
                job_id=job.id,
                job_name=job_name,
                job_variant=job_variant,
                step_name=failed_step,
                conclusion=job.conclusion,
                html_url=job.html_url,
                created_at=datetime.now(tz=UTC),
            )
            session.add(job_failure)
            await session.flush()

            parsed = await self._parse_job_failure(project_name, job)
            for failure in parsed:
                session.add(
                    CITestFailure(
                        job_failure_id=job_failure.id,
                        test_type=failure.test_type,
                        signature=failure.signature,
                        error_message=failure.error_message,
                        is_environmental=failure.is_environmental,
                    )
                )
            failures_recorded += len(parsed) or 1
        return failures_recorded

    async def _parse_job_failure(
        self, project_name: str, job: WorkflowJob
    ) -> list[Any]:
        """Return parsed failure signatures for one failed job.

        Prefers Check Run Annotations (structured, zero log downloads); only
        falls back to downloading and parsing the job's log tail when no
        annotations are available.
        """
        annotations = await self._fetch_annotations(project_name, job)
        if annotations:
            return parse_check_annotations(annotations)

        log_tail = await self._fetch_log_tail(project_name, job.id)
        if not log_tail:
            return []
        return parse_log_tail(log_tail, job_name=job.name)

    async def _fetch_annotations(
        self, project_name: str, job: WorkflowJob
    ) -> list[dict[str, Any]]:
        """Fetch Check Run Annotations for a job via its check_run_url."""
        check_run_url = job.check_run_url
        if not check_run_url:
            return []
        try:
            check_run_id = int(check_run_url.rstrip("/").rsplit("/", 1)[-1])
            repo: GHRepository = self.gh.get_repo(f"{self.org}/{project_name}")
            check_run = repo.get_check_run(check_run_id)
            return [a.raw_data for a in check_run.get_annotations()]
        except Exception:  # noqa: BLE001 - annotations are a best-effort optimization
            logger.debug(
                "Could not fetch annotations for job %d (%s)",
                job.id,
                project_name,
                exc_info=True,
            )
            return []

    @retry(
        retry=retry_if_exception(is_retriable_http_error),
        wait=make_wait_honoring_retry_after(
            wait_exponential(multiplier=2, min=4, max=60)
        ),
        stop=stop_after_attempt(_MAX_HTTP_ATTEMPTS),
        before_sleep=make_before_sleep_log(
            logger, _MAX_HTTP_ATTEMPTS, level=logging.DEBUG, prefix="CI log fetch retry"
        ),
        reraise=True,
    )
    async def _fetch_log_tail(self, project_name: str, job_id: int) -> str:
        """Stream only the tail of a job's log, bounded to _LOG_TAIL_LINES.

        PyGithub does not follow the redirect GitHub issues for this
        endpoint (to short-lived blob storage), so this uses httpx directly,
        same as the `http_retry` policy used for every other outbound
        integration in this app.
        """
        url = f"https://api.github.com/repos/{self.org}/{project_name}/actions/jobs/{job_id}/logs"
        tail: deque[str] = deque(maxlen=_LOG_TAIL_LINES)
        try:
            async with self.http.stream("GET", url) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    tail.append(line)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (_HTTP_GONE, _HTTP_NOT_FOUND):
                # Logs expired (GitHub only retains them briefly); nothing
                # to parse, not an error worth retrying or surfacing.
                return ""
            raise
        return "\n".join(tail)
