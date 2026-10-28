"""Unit tests for the GitHub Actions CI failure collector."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from craft_dashboard.collectors.ci_collector import (
    CIFailureCollector,
    _parse_job_variant,
)
from craft_dashboard.models.ci_failure import CIJobFailure, CITestFailure, CIWorkflowRun
from craft_dashboard.models.project import Project
from sqlalchemy import select


def _make_run(
    *,
    run_id: int = 1,
    run_attempt: int = 1,
    name: str = "CI",
    conclusion: str | None = "success",
    event: str = "push",
    head_branch: str = "main",
    is_fork: bool = False,
    created_at: datetime | None = None,
    jobs: list | None = None,
) -> MagicMock:
    run = MagicMock()
    run.id = run_id
    run.run_attempt = run_attempt
    run.name = name
    run.head_branch = head_branch
    run.head_sha = "deadbeef"
    run.event = event
    run.status = "completed"
    run.conclusion = conclusion
    run.run_started_at = created_at
    run.updated_at = created_at
    run.created_at = created_at or datetime.now(tz=UTC)
    run.html_url = f"https://github.com/canonical/foo/actions/runs/{run_id}"
    run.head_repository = MagicMock(fork=is_fork)
    run.jobs.return_value = jobs or []
    return run


def _make_job(
    *,
    job_id: int = 1,
    name: str = "unit-tests",
    conclusion: str = "failure",
    failed_step: str = "Run tests",
    check_run_url: str
    | None = "https://api.github.com/repos/canonical/foo/check-runs/999",
) -> MagicMock:
    job = MagicMock()
    job.id = job_id
    job.name = name
    job.conclusion = conclusion
    job.check_run_url = check_run_url
    job.html_url = f"https://github.com/canonical/foo/actions/runs/1/jobs/{job_id}"
    step = MagicMock(name=failed_step, conclusion="failure")
    step.name = failed_step
    job.steps = [step]
    return job


@pytest.fixture
async def project_id(test_db_session) -> int:
    project = Project(name="foo", category="application")
    test_db_session.add(project)
    await test_db_session.commit()
    return project.id


class TestParseJobVariant:
    def test_splits_matrix_job_name(self):
        base, variant = _parse_job_variant(
            "integration-spread-tests (openstack:ubuntu-24.04-64)"
        )
        assert base == "integration-spread-tests"
        assert variant == "openstack:ubuntu-24.04-64"

    def test_non_matrix_job_has_no_variant(self):
        assert _parse_job_variant("unit-tests") == ("unit-tests", None)


class TestResolveSince:
    async def test_no_existing_runs_uses_max_lookback(
        self, test_db_session, project_id
    ):
        collector = CIFailureCollector(token="x")
        since = await collector._resolve_since(test_db_session, project_id)
        expected_floor = datetime.now(tz=UTC) - timedelta(days=14)
        assert abs((since - expected_floor).total_seconds()) < 5

    async def test_uses_oldest_eligible_run_last_fetched(
        self, test_db_session, project_id
    ):
        old_fetch = datetime.now(tz=UTC) - timedelta(days=3)
        test_db_session.add(
            CIWorkflowRun(
                project_id=project_id,
                run_id=1,
                run_attempt=1,
                workflow_name="CI",
                event="push",
                status="in_progress",
                conclusion=None,
                last_fetched_at=old_fetch,
            )
        )
        await test_db_session.commit()
        collector = CIFailureCollector(token="x")
        since = await collector._resolve_since(test_db_session, project_id)
        assert since <= old_fetch - timedelta(days=1) + timedelta(seconds=5)

    async def test_completed_run_past_rerun_grace_is_not_eligible(
        self, test_db_session, project_id
    ):
        stale_fetch = datetime.now(tz=UTC) - timedelta(days=30)
        test_db_session.add(
            CIWorkflowRun(
                project_id=project_id,
                run_id=1,
                run_attempt=1,
                workflow_name="CI",
                event="push",
                status="completed",
                conclusion="success",
                completed_at=datetime.now(tz=UTC) - timedelta(days=30),
                last_fetched_at=stale_fetch,
            )
        )
        await test_db_session.commit()
        collector = CIFailureCollector(token="x")
        since = await collector._resolve_since(test_db_session, project_id)
        # Should fall back to the max lookback cap, not the stale run's
        # last_fetched_at, since that run is no longer rerun-eligible.
        expected_floor = datetime.now(tz=UTC) - timedelta(days=14)
        assert abs((since - expected_floor).total_seconds()) < 5


class TestFetchLogTail:
    async def test_returns_empty_on_gone(self):
        collector = CIFailureCollector(token="x")
        transport = httpx.MockTransport(
            lambda request: httpx.Response(410, request=request)
        )
        collector._http = httpx.AsyncClient(transport=transport)

        assert await collector._fetch_log_tail("foo", 1) == ""

    async def test_returns_empty_on_blob_storage_not_found(self):
        # The redirect target (short-lived blob storage) can 404 if the log
        # was evicted between the redirect being issued and the fetch
        # landing -- a race, not a real error, and must not abort the whole
        # project's CI collection pass the way an unhandled exception would.
        collector = CIFailureCollector(token="x")
        transport = httpx.MockTransport(
            lambda request: httpx.Response(404, request=request)
        )
        collector._http = httpx.AsyncClient(transport=transport)

        assert await collector._fetch_log_tail("foo", 1) == ""


class TestCollect:
    async def test_records_success_run_without_job_failures(
        self, test_db_session, project_id
    ):
        collector = CIFailureCollector(token="x")
        run = _make_run(conclusion="success")
        collector.gh = MagicMock()
        collector.gh.get_repo.return_value.get_workflow_runs.return_value = [run]

        count = await collector.collect("foo", project_id, test_db_session)

        assert count == 0
        stored = (await test_db_session.execute(select(CIWorkflowRun))).scalars().all()
        assert len(stored) == 1
        assert stored[0].conclusion == "success"

    async def test_skips_fork_pr_runs_by_default(self, test_db_session, project_id):
        collector = CIFailureCollector(token="x")
        run = _make_run(event="pull_request", is_fork=True, conclusion="failure")
        collector.gh = MagicMock()
        collector.gh.get_repo.return_value.get_workflow_runs.return_value = [run]

        await collector.collect("foo", project_id, test_db_session)

        stored = (await test_db_session.execute(select(CIWorkflowRun))).scalars().all()
        assert stored == []

    async def test_records_job_failure_from_annotations(
        self, test_db_session, project_id
    ):
        # job.name has no structured-parser hint (spread/pytest/go), so
        # annotations are used directly without a log-tail download first.
        job = _make_job(name="security-scan")
        run = _make_run(conclusion="failure", jobs=[job])
        collector = CIFailureCollector(token="x")
        collector.gh = MagicMock()
        collector.gh.get_repo.return_value.get_workflow_runs.return_value = [run]
        annotation = MagicMock()
        annotation.raw_data = {
            "path": "tests/unit/test_foo.py",
            "start_line": 10,
            "message": "AssertionError",
            "annotation_level": "failure",
        }
        collector.gh.get_repo.return_value.get_check_run.return_value.get_annotations.return_value = [
            annotation
        ]

        count = await collector.collect("foo", project_id, test_db_session)

        assert count == 1
        job_failures = (
            (await test_db_session.execute(select(CIJobFailure))).scalars().all()
        )
        assert len(job_failures) == 1
        assert job_failures[0].step_name == "Run tests"
        test_failures = (
            (await test_db_session.execute(select(CITestFailure))).scalars().all()
        )
        assert test_failures[0].signature == "tests/unit/test_foo.py:10"

    async def test_prefers_log_tail_signature_over_annotations_for_structured_job(
        self, test_db_session, project_id
    ):
        # A job name hinting a known structured parser (pytest, here) should
        # get its stable test-name signature even though annotations also
        # exist -- annotations fall back to brittle path:line, which would
        # never show this test as recurring once a nearby line shifts.
        job = _make_job(name="unit-tests")
        run = _make_run(conclusion="failure", jobs=[job])
        collector = CIFailureCollector(token="x")
        collector.gh = MagicMock()
        collector.gh.get_repo.return_value.get_workflow_runs.return_value = [run]
        collector._fetch_log_tail = AsyncMock(
            return_value="FAILED tests/unit/test_bar.py::test_baz - ValueError\n"
        )
        annotation = MagicMock()
        annotation.raw_data = {
            "path": "tests/unit/test_bar.py",
            "start_line": 42,
            "message": "ValueError",
            "annotation_level": "failure",
        }
        collector.gh.get_repo.return_value.get_check_run.return_value.get_annotations.return_value = [
            annotation
        ]

        await collector.collect("foo", project_id, test_db_session)

        test_failures = (
            (await test_db_session.execute(select(CITestFailure))).scalars().all()
        )
        assert test_failures[0].signature == "tests/unit/test_bar.py::test_baz"
        assert test_failures[0].test_type == "pytest"

    async def test_falls_back_to_annotations_when_structured_parser_finds_nothing(
        self, test_db_session, project_id
    ):
        # The job name hints a structured parser, but the log tail yielded
        # no match (e.g. logs already expired) -- fall back to annotations
        # rather than recording a signature-less job failure.
        job = _make_job(name="unit-tests")
        run = _make_run(conclusion="failure", jobs=[job])
        collector = CIFailureCollector(token="x")
        collector.gh = MagicMock()
        collector.gh.get_repo.return_value.get_workflow_runs.return_value = [run]
        collector._fetch_log_tail = AsyncMock(return_value="")
        annotation = MagicMock()
        annotation.raw_data = {
            "path": "tests/unit/test_foo.py",
            "start_line": 10,
            "message": "AssertionError",
            "annotation_level": "failure",
        }
        collector.gh.get_repo.return_value.get_check_run.return_value.get_annotations.return_value = [
            annotation
        ]

        await collector.collect("foo", project_id, test_db_session)

        test_failures = (
            (await test_db_session.execute(select(CITestFailure))).scalars().all()
        )
        assert test_failures[0].signature == "tests/unit/test_foo.py:10"

    async def test_falls_back_to_log_tail_when_no_annotations(
        self, test_db_session, project_id
    ):
        job = _make_job(check_run_url=None)
        run = _make_run(conclusion="failure", jobs=[job])
        collector = CIFailureCollector(token="x")
        collector.gh = MagicMock()
        collector.gh.get_repo.return_value.get_workflow_runs.return_value = [run]
        collector._fetch_log_tail = AsyncMock(
            return_value="FAILED tests/unit/test_bar.py::test_baz - ValueError\n"
        )

        count = await collector.collect("foo", project_id, test_db_session)

        assert count == 1
        test_failures = (
            (await test_db_session.execute(select(CITestFailure))).scalars().all()
        )
        assert test_failures[0].signature == "tests/unit/test_bar.py::test_baz"

    async def test_job_failure_with_no_parsed_signature_is_still_recorded(
        self, test_db_session, project_id
    ):
        job = _make_job(check_run_url=None)
        run = _make_run(conclusion="failure", jobs=[job])
        collector = CIFailureCollector(token="x")
        collector.gh = MagicMock()
        collector.gh.get_repo.return_value.get_workflow_runs.return_value = [run]
        collector._fetch_log_tail = AsyncMock(return_value="")

        count = await collector.collect("foo", project_id, test_db_session)

        assert count == 1
        job_failures = (
            (await test_db_session.execute(select(CIJobFailure))).scalars().all()
        )
        assert len(job_failures) == 1
        test_failures = (
            (await test_db_session.execute(select(CITestFailure))).scalars().all()
        )
        assert test_failures == []

    async def test_second_pass_does_not_duplicate_still_failing_job(
        self, test_db_session, project_id
    ):
        # A job that's still within its rerun-eligible window gets refetched
        # by every collection pass; without a (workflow_run_id, job_id)
        # idempotency check this would insert a second CIJobFailure row and
        # inflate the signature's occurrence count past the actual run count.
        job = _make_job(check_run_url=None)
        run = _make_run(conclusion="failure", jobs=[job])
        collector = CIFailureCollector(token="x")
        collector.gh = MagicMock()
        collector.gh.get_repo.return_value.get_workflow_runs.return_value = [run]
        collector._fetch_log_tail = AsyncMock(
            return_value="FAILED tests/unit/test_bar.py::test_baz - ValueError\n"
        )

        await collector.collect("foo", project_id, test_db_session)
        count = await collector.collect("foo", project_id, test_db_session)

        assert count == 0
        job_failures = (
            (await test_db_session.execute(select(CIJobFailure))).scalars().all()
        )
        assert len(job_failures) == 1
        test_failures = (
            (await test_db_session.execute(select(CITestFailure))).scalars().all()
        )
        assert len(test_failures) == 1

    async def test_rerun_updates_existing_row_in_place(
        self, test_db_session, project_id
    ):
        collector = CIFailureCollector(token="x")
        first_run = _make_run(conclusion="failure", run_attempt=1)
        collector.gh = MagicMock()
        collector.gh.get_repo.return_value.get_workflow_runs.return_value = [first_run]
        collector._fetch_log_tail = AsyncMock(return_value="")
        await collector.collect("foo", project_id, test_db_session)

        rerun = _make_run(conclusion="success", run_attempt=2, run_id=first_run.id)
        collector.gh.get_repo.return_value.get_workflow_runs.return_value = [rerun]
        await collector.collect("foo", project_id, test_db_session)

        stored = (await test_db_session.execute(select(CIWorkflowRun))).scalars().all()
        assert len(stored) == 2
        attempts = {row.run_attempt: row.conclusion for row in stored}
        assert attempts == {1: "failure", 2: "success"}
