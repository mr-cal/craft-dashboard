"""Unit tests for the CI failure catalog routes."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.config import CIConfig, DashboardConfig
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.ci_failure import (
    CIFailureDailyCount,
    CIJobFailure,
    CITestFailure,
    CIWorkflowRun,
)
from craft_dashboard.models.project import Project
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

_NOW = datetime.now(tz=UTC)


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


@pytest.fixture
def test_client(test_db_session: AsyncSession) -> AsyncGenerator[TestClient, None]:
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.state.config = DashboardConfig(ci=CIConfig())

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield test_db_session

    app.dependency_overrides[get_db_session] = _override

    with TestClient(app) as client:
        yield client


def _project(name: str) -> Project:
    return Project(
        name=name, category="application", github_org="canonical", display_order=10
    )


async def _seed_failure(
    session: AsyncSession,
    *,
    project: Project,
    run_id: int,
    workflow_name: str = "Tests",
    job_name: str = "unit-tests",
    signature: str = "AssertionError: boom",
    test_type: str = "pytest",
    head_branch: str = "main",
    event: str = "push",
    conclusion: str = "failure",
    run_attempt: int = 1,
    when: datetime | None = None,
    error_message: str | None = None,
) -> None:
    when = when or _NOW
    run = CIWorkflowRun(
        project_id=project.id,
        run_id=run_id,
        run_attempt=run_attempt,
        workflow_name=workflow_name,
        head_branch=head_branch,
        head_sha="abc123",
        event=event,
        is_fork_pr=False,
        status="completed",
        conclusion=conclusion,
        started_at=when,
        completed_at=when,
        last_fetched_at=when,
        html_url=f"https://github.com/canonical/{project.name}/actions/runs/{run_id}",
    )
    session.add(run)
    await session.flush()

    if conclusion == "failure":
        job = CIJobFailure(
            workflow_run_id=run.id,
            project_id=project.id,
            job_id=run_id * 10,
            job_name=job_name,
            job_variant=None,
            step_name="Run tests",
            conclusion="failure",
            html_url=run.html_url,
            created_at=when,
        )
        session.add(job)
        await session.flush()

        test_failure = CITestFailure(
            job_failure_id=job.id,
            test_type=test_type,
            signature=signature,
            error_message=error_message or f"{signature} at test_module.py:42",
            is_environmental=False,
        )
        session.add(test_failure)

    await session.commit()


@pytest.mark.asyncio
async def test_catalog_project_only_filter(test_client: TestClient, test_db_session):
    proj_a = _project("snapcraft")
    proj_b = _project("charmcraft")
    test_db_session.add_all([proj_a, proj_b])
    await test_db_session.commit()

    for i in range(12):
        await _seed_failure(test_db_session, project=proj_a, run_id=1000 + i)
    for i in range(12):
        await _seed_failure(
            test_db_session,
            project=proj_b,
            run_id=2000 + i,
            signature="OtherError: nope",
        )

    response = test_client.get("/stats/ci?project=snapcraft")
    assert response.status_code == 200
    assert "snapcraft" in response.text
    assert "OtherError" not in response.text


@pytest.mark.asyncio
async def test_catalog_project_and_workflow_filter(
    test_client: TestClient, test_db_session
):
    proj = _project("snapcraft")
    test_db_session.add(proj)
    await test_db_session.commit()

    for i in range(12):
        await _seed_failure(
            test_db_session, project=proj, run_id=3000 + i, workflow_name="Tests"
        )
    for i in range(12):
        await _seed_failure(
            test_db_session,
            project=proj,
            run_id=4000 + i,
            workflow_name="Spread",
            signature="SpreadError: nope",
        )

    response = test_client.get("/stats/ci/table?project=snapcraft&workflow=Spread")
    assert response.status_code == 200
    assert "SpreadError" in response.text
    assert "AssertionError" not in response.text


@pytest.mark.asyncio
async def test_catalog_branch_filter(test_client: TestClient, test_db_session):
    proj = _project("snapcraft")
    test_db_session.add(proj)
    await test_db_session.commit()

    for i in range(12):
        await _seed_failure(
            test_db_session,
            project=proj,
            run_id=5000 + i,
            head_branch="main",
            event="push",
            signature="MainError: nope",
        )
    for i in range(12):
        await _seed_failure(
            test_db_session,
            project=proj,
            run_id=6000 + i,
            head_branch="feature-x",
            event="pull_request",
            signature="PRError: nope",
        )

    response = test_client.get("/stats/ci/table?project=snapcraft&branch=main")
    assert response.status_code == 200
    assert "MainError" in response.text
    assert "PRError" not in response.text


@pytest.mark.asyncio
async def test_catalog_default_sort_is_fail_rate_desc(
    test_client: TestClient, test_db_session
):
    proj = _project("snapcraft")
    test_db_session.add(proj)
    await test_db_session.commit()

    # High fail rate: 10/10 failing.
    for i in range(10):
        await _seed_failure(
            test_db_session,
            project=proj,
            run_id=7000 + i,
            signature="HighRateError",
        )
    # Low fail rate: 1 failure in 10 otherwise-passing runs.
    await _seed_failure(
        test_db_session, project=proj, run_id=8000, signature="LowRateError"
    )
    for i in range(9):
        await _seed_failure(
            test_db_session,
            project=proj,
            run_id=8001 + i,
            conclusion="success",
        )

    response = test_client.get("/stats/ci/table?project=snapcraft")
    assert response.status_code == 200
    high_idx = response.text.find("HighRateError")
    low_idx = response.text.find("LowRateError")
    assert high_idx != -1
    assert low_idx != -1
    assert high_idx < low_idx


@pytest.mark.asyncio
async def test_signature_detail_renders_with_no_recent_runs(
    test_client: TestClient, test_db_session
):
    proj = _project("snapcraft")
    test_db_session.add(proj)
    await test_db_session.commit()

    # Only a rollup row survives (past the detail retention window); no
    # CIWorkflowRun/CIJobFailure/CITestFailure rows remain for this signature.
    daily = CIFailureDailyCount(
        project_id=proj.id,
        signature="AssertionError: boom",
        branch_class="main",
        day=date.today() - timedelta(days=5),
        fail_count=3,
        total_count=20,
    )
    test_db_session.add(daily)
    await test_db_session.commit()

    response = test_client.get(
        "/stats/ci/signature"
        "?project=snapcraft&workflow=Tests&job=unit-tests&type=pytest"
        "&signature=" + "AssertionError%3A%20boom"
    )
    assert response.status_code == 200
    assert "No individual runs left" in response.text
    assert "AssertionError" in response.text


@pytest.mark.asyncio
async def test_signature_detail_shows_sample_error_message(
    test_client: TestClient, test_db_session
):
    proj = _project("snapcraft")
    test_db_session.add(proj)
    await test_db_session.commit()

    await _seed_failure(test_db_session, project=proj, run_id=1)

    response = test_client.get(
        "/stats/ci/signature"
        "?project=snapcraft&workflow=Tests&job=unit-tests&type=pytest"
        "&signature=" + "AssertionError%3A%20boom"
    )
    assert response.status_code == 200
    # The sample error message is shown once near the signature header, in
    # addition to the recent-runs table row -- a bare signature like
    # ".github:98" is meaningless without the text that produced it.
    assert response.text.count("AssertionError: boom at test_module.py:42") >= 2


@pytest.mark.asyncio
async def test_signature_detail_recent_runs_expandable_and_untruncated(
    test_client: TestClient, test_db_session
):
    proj = _project("snapcraft")
    test_db_session.add(proj)
    await test_db_session.commit()

    # Well past the old 200-char server-side cut-off; the tail must reach the
    # page so a click can reveal it.
    long_error = "AssertionError: boom " + ("x" * 400) + " END-OF-ERROR"
    await _seed_failure(
        test_db_session, project=proj, run_id=1, error_message=long_error
    )

    response = test_client.get(
        "/stats/ci/signature"
        "?project=snapcraft&workflow=Tests&job=unit-tests&type=pytest"
        "&signature=" + "AssertionError%3A%20boom"
    )
    assert response.status_code == 200
    text = response.text
    assert "END-OF-ERROR" in text
    assert 'class="summary-cell"' in text
    assert "/static/js/table-interact.js" in text
    assert "row--25-75" in text
    assert "<colgroup>" in text
    # Shared local-time filter, not a hand-rolled strftime.
    assert '<time class="local-time"' in text


@pytest.mark.asyncio
async def test_catalog_last_seen_uses_local_time(
    test_client: TestClient, test_db_session
):
    proj = _project("snapcraft")
    test_db_session.add(proj)
    await test_db_session.commit()
    for i in range(12):
        await _seed_failure(test_db_session, project=proj, run_id=5000 + i)

    response = test_client.get("/stats/ci?project=snapcraft")
    assert response.status_code == 200
    assert '<time class="local-time"' in response.text
