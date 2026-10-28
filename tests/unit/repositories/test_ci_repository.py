"""Tests for the CI failure catalog repository."""

from datetime import UTC, datetime

from craft_dashboard.models.ci_failure import CIJobFailure, CITestFailure, CIWorkflowRun
from craft_dashboard.models.project import Project
from craft_dashboard.models.views import CIFilters
from craft_dashboard.repositories.ci_repository import CIRepository, branch_class_for

_NOW = datetime.now(tz=UTC)


async def _add_project(session, name="snapcraft") -> Project:
    project = Project(name=name, category="application", display_order=0)
    session.add(project)
    await session.flush()
    return project


async def _add_run(
    session,
    project,
    *,
    run_id=1,
    run_attempt=1,
    workflow_name="CI",
    event="pull_request",
    head_branch="feature-x",
    conclusion="failure",
    started_at=_NOW,
) -> CIWorkflowRun:
    run = CIWorkflowRun(
        project_id=project.id,
        run_id=run_id,
        run_attempt=run_attempt,
        workflow_name=workflow_name,
        head_branch=head_branch,
        head_sha="a" * 40,
        event=event,
        is_fork_pr=False,
        status="completed",
        conclusion=conclusion,
        started_at=started_at,
        completed_at=started_at,
        last_fetched_at=_NOW,
        html_url="https://github.com/canonical/snapcraft/actions/runs/1",
    )
    session.add(run)
    await session.flush()
    return run


async def _add_job_failure(
    session, run, project, *, job_name="unit-tests", job_variant=None
) -> CIJobFailure:
    job = CIJobFailure(
        workflow_run_id=run.id,
        project_id=project.id,
        job_id=run.run_id * 10,
        job_name=job_name,
        job_variant=job_variant,
        step_name="Run tests",
        conclusion="failure",
        html_url="https://github.com/canonical/snapcraft/actions/runs/1/jobs/1",
        created_at=_NOW,
    )
    session.add(job)
    await session.flush()
    return job


async def _add_test_failure(
    session, job, *, signature="tests/unit/test_foo.py::test_bar", test_type="pytest"
) -> CITestFailure:
    failure = CITestFailure(
        job_failure_id=job.id,
        test_type=test_type,
        signature=signature,
        error_message="AssertionError: boom",
        is_environmental=False,
    )
    session.add(failure)
    await session.flush()
    return failure


def test_branch_class_for_push_to_trunk():
    assert branch_class_for("push", "main", ["main"]) == "main"


def test_branch_class_for_pr_not_matching_trunk():
    assert branch_class_for("pull_request", "feature", ["main"]) == "pr"


def test_branch_class_for_merge_group_is_main():
    assert branch_class_for("merge_group", None, ["main"]) == "main"


def test_branch_class_for_schedule_is_scheduled():
    assert branch_class_for("schedule", None, ["main"]) == "scheduled"


def test_branch_class_for_dynamic_is_other():
    assert branch_class_for("dynamic", None, ["main"]) == "other"


def test_branch_class_for_hotfix_glob():
    assert branch_class_for("push", "hotfix/6.1", ["main", "hotfix/*"]) == "main"


async def test_search_filters_by_project(test_db_session):
    session = test_db_session
    snapcraft = await _add_project(session, "snapcraft")
    rockcraft = await _add_project(session, "rockcraft")
    for i in range(10):
        run = await _add_run(session, snapcraft, run_id=i)
        job = await _add_job_failure(session, run, snapcraft)
        await _add_test_failure(session, job)
    for i in range(10):
        run = await _add_run(session, rockcraft, run_id=100 + i)
        job = await _add_job_failure(session, run, rockcraft)
        await _add_test_failure(session, job, signature="other::test")

    repo = CIRepository(session)
    result = await repo.search(
        CIFilters(project="snapcraft"),
        trunk_branches_by_project={"snapcraft": ["main"], "rockcraft": ["main"]},
    )
    assert len(result.rows) == 1
    assert result.rows[0].project_name == "snapcraft"
    assert result.rows[0].occurrence_count == 10
    assert result.rows[0].total_count == 10


async def test_search_filters_by_project_and_workflow(test_db_session):
    session = test_db_session
    project = await _add_project(session)
    for i in range(10):
        run = await _add_run(session, project, run_id=i, workflow_name="CI")
        job = await _add_job_failure(session, run, project)
        await _add_test_failure(session, job)
    for i in range(10):
        run = await _add_run(session, project, run_id=200 + i, workflow_name="Lint")
        job = await _add_job_failure(session, run, project)
        await _add_test_failure(session, job, signature="ruff::E501")

    repo = CIRepository(session)
    result = await repo.search(
        CIFilters(project="snapcraft", workflow="Lint"),
        trunk_branches_by_project={"snapcraft": ["main"]},
    )
    assert len(result.rows) == 1
    assert result.rows[0].workflow_name == "Lint"
    assert result.rows[0].signature == "ruff::E501"


async def test_search_filters_by_branch_class(test_db_session):
    session = test_db_session
    project = await _add_project(session)
    for i in range(10):
        run = await _add_run(
            session, project, run_id=i, event="push", head_branch="main"
        )
        job = await _add_job_failure(session, run, project)
        await _add_test_failure(session, job, signature="main-failure")
    for i in range(10):
        run = await _add_run(
            session, project, run_id=300 + i, event="pull_request", head_branch="feat"
        )
        job = await _add_job_failure(session, run, project)
        await _add_test_failure(session, job, signature="pr-failure")

    repo = CIRepository(session)
    result = await repo.search(
        CIFilters(project="snapcraft", branch_class="main"),
        trunk_branches_by_project={"snapcraft": ["main"]},
    )
    assert len(result.rows) == 1
    assert result.rows[0].signature == "main-failure"
    assert result.rows[0].branch_class == "main"


async def test_same_signature_on_different_branch_classes_does_not_inflate_occurrence_count(
    test_db_session,
):
    """A signature failing on both pr and main runs must split into two rows.

    Folding both into one group would divide occurrence_count (summed
    across branch classes) by a single branch class's total_count,
    producing a fail rate above 100%.
    """
    session = test_db_session
    project = await _add_project(session)
    for i in range(2):
        run = await _add_run(
            session, project, run_id=i, event="push", head_branch="main"
        )
        job = await _add_job_failure(session, run, project)
        await _add_test_failure(session, job, signature="shared-failure")
    for i in range(10):
        run = await _add_run(
            session, project, run_id=300 + i, event="pull_request", head_branch="feat"
        )
        job = await _add_job_failure(session, run, project)
        await _add_test_failure(session, job, signature="shared-failure")

    repo = CIRepository(session)
    result = await repo.search(
        CIFilters(project="snapcraft"),
        trunk_branches_by_project={"snapcraft": ["main"]},
    )
    rows_by_branch_class = {row.branch_class: row for row in result.rows}
    assert set(rows_by_branch_class) == {"main", "pr"}
    assert rows_by_branch_class["main"].occurrence_count == 2
    assert rows_by_branch_class["main"].total_count == 2
    assert rows_by_branch_class["pr"].occurrence_count == 10
    assert rows_by_branch_class["pr"].total_count == 10
    for row in result.rows:
        assert row.occurrence_count <= row.total_count


async def test_fail_rate_excludes_superseded_rerun_attempts(test_db_session):
    """A rerun that fixed the failure should not still count as failing."""
    session = test_db_session
    project = await _add_project(session)
    # Attempt 1 fails...
    run1 = await _add_run(
        session, project, run_id=1, run_attempt=1, conclusion="failure"
    )
    job1 = await _add_job_failure(session, run1, project)
    await _add_test_failure(session, job1)
    # ...attempt 2 (the rerun) succeeds. Only the latest attempt counts.
    await _add_run(session, project, run_id=1, run_attempt=2, conclusion="success")
    # 9 more runs, all passing, to get above the 10-run floor.
    for i in range(2, 11):
        await _add_run(session, project, run_id=i, conclusion="success")

    repo = CIRepository(session)
    result = await repo.search(
        CIFilters(project="snapcraft"),
        trunk_branches_by_project={"snapcraft": ["main"]},
    )
    assert result.rows == []  # the only failure was superseded by its rerun


async def test_insufficient_data_below_minimum_runs(test_db_session):
    session = test_db_session
    project = await _add_project(session)
    run = await _add_run(session, project, run_id=1)
    job = await _add_job_failure(session, run, project)
    await _add_test_failure(session, job)

    repo = CIRepository(session)
    result = await repo.search(
        CIFilters(project="snapcraft"),
        trunk_branches_by_project={"snapcraft": ["main"]},
    )
    assert len(result.rows) == 1
    assert result.rows[0].total_count == 1
    assert result.rows[0].fail_rate is None


async def test_default_sort_is_fail_rate_desc_then_occurrence_count_desc(
    test_db_session,
):
    session = test_db_session
    project = await _add_project(session)
    # Signature A: fails 2/10 runs.
    for i in range(10):
        run = await _add_run(
            session, project, run_id=i, conclusion="failure" if i < 2 else "success"
        )
        if i < 2:
            job = await _add_job_failure(session, run, project, job_name="job-a")
            await _add_test_failure(session, job, signature="sig-a")
    # Signature B: fails 8/10 runs (different workflow so denominators differ).
    for i in range(10):
        run = await _add_run(
            session,
            project,
            run_id=100 + i,
            workflow_name="OtherCI",
            conclusion="failure" if i < 8 else "success",
        )
        if i < 8:
            job = await _add_job_failure(session, run, project, job_name="job-b")
            await _add_test_failure(session, job, signature="sig-b")

    repo = CIRepository(session)
    result = await repo.search(
        CIFilters(project="snapcraft"),
        trunk_branches_by_project={"snapcraft": ["main"]},
    )
    assert [row.signature for row in result.rows] == ["sig-b", "sig-a"]


async def test_flip_flop_count_counts_reruns_with_differing_outcomes(test_db_session):
    session = test_db_session
    project = await _add_project(session)
    # This run flip-flopped: attempt 1 failed, attempt 2 succeeded -- but a
    # later rerun (attempt 3) failed again, so the *latest* attempt is a
    # failure whose outcome differs from an earlier attempt.
    await _add_run(session, project, run_id=1, run_attempt=1, conclusion="failure")
    await _add_run(session, project, run_id=1, run_attempt=2, conclusion="success")
    run3 = await _add_run(
        session, project, run_id=1, run_attempt=3, conclusion="failure"
    )
    job = await _add_job_failure(session, run3, project)
    await _add_test_failure(session, job)
    for i in range(2, 11):
        await _add_run(session, project, run_id=i, conclusion="success")

    repo = CIRepository(session)
    result = await repo.search(
        CIFilters(project="snapcraft"),
        trunk_branches_by_project={"snapcraft": ["main"]},
    )
    assert len(result.rows) == 1
    assert result.rows[0].flip_flop_count == 1


async def test_jobs_without_test_failures_roll_up_at_job_level(test_db_session):
    session = test_db_session
    project = await _add_project(session)
    run = await _add_run(session, project, run_id=1)
    await _add_job_failure(session, run, project, job_name="build")

    repo = CIRepository(session)
    result = await repo.search(
        CIFilters(project="snapcraft"),
        trunk_branches_by_project={"snapcraft": ["main"]},
    )
    assert len(result.rows) == 1
    assert result.rows[0].test_type == "job"
    assert result.rows[0].signature == "build"
