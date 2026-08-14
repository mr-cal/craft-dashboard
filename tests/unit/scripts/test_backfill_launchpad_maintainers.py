"""Tests for scripts.backfill_launchpad_maintainers."""

from __future__ import annotations

from click.testing import CliRunner
from craft_dashboard.config import DashboardConfig
from craft_dashboard.models.base import Base
from craft_dashboard.models.issue import Issue
from scripts import backfill_launchpad_maintainers
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session

from tests.factories import make_issue, make_project

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"


class _FakeSettings:
    """Stand-in for craft_dashboard.settings.Settings in tests."""

    database_url = "postgresql+asyncpg://localhost/test"
    config_file = "craft-dashboard.toml"


def _make_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def _seed_issues(engine) -> None:
    with Session(engine) as session:
        project = make_project(name="snapcraft (launchpad)")
        session.add(project)
        session.flush()
        session.add_all(
            [
                make_issue(
                    project_id=project.id,
                    source="launchpad",
                    external_id="1",
                    author="~cmatsuoka",
                    author_is_maintainer=False,
                ),
                make_issue(
                    project_id=project.id,
                    source="launchpad",
                    external_id="2",
                    author="~sergiusens",
                    author_is_maintainer=True,
                ),
                make_issue(
                    project_id=project.id,
                    source="launchpad",
                    external_id="3",
                    author="~random-reporter",
                    author_is_maintainer=False,
                ),
            ]
        )
        session.commit()


def test_backfill_updates_stale_author_is_maintainer(monkeypatch) -> None:
    """Issues get author_is_maintainer recomputed from the current config."""
    engine = _make_engine()
    _seed_issues(engine)

    monkeypatch.setattr(
        backfill_launchpad_maintainers,
        "create_engine",
        lambda _url: engine,
    )
    monkeypatch.setattr(
        backfill_launchpad_maintainers,
        "Settings",
        _FakeSettings,
    )
    monkeypatch.setattr(
        backfill_launchpad_maintainers,
        "load_config",
        lambda _path: DashboardConfig(
            maintainers=["cmatsuoka", "sergiusens"],
            launchpad_maintainers=["~cmatsuoka", "~sergiusens"],
            craft_projects=["snapcraft"],
        ),
    )

    runner = CliRunner()
    result = runner.invoke(backfill_launchpad_maintainers.main, [])

    assert result.exit_code == 0, result.output

    with Session(engine) as session:
        issues = {
            issue.author: issue.author_is_maintainer
            for issue in session.execute(select(Issue)).scalars()
        }
    assert issues["~cmatsuoka"] is True
    assert issues["~sergiusens"] is True
    assert issues["~random-reporter"] is False


def test_backfill_dry_run_does_not_write(monkeypatch) -> None:
    """--dry-run reports changes without persisting them."""
    engine = _make_engine()
    _seed_issues(engine)

    monkeypatch.setattr(
        backfill_launchpad_maintainers,
        "create_engine",
        lambda _url: engine,
    )
    monkeypatch.setattr(
        backfill_launchpad_maintainers,
        "Settings",
        _FakeSettings,
    )
    monkeypatch.setattr(
        backfill_launchpad_maintainers,
        "load_config",
        lambda _path: DashboardConfig(
            maintainers=["cmatsuoka", "sergiusens"],
            launchpad_maintainers=["~cmatsuoka", "~sergiusens"],
            craft_projects=["snapcraft"],
        ),
    )

    runner = CliRunner()
    result = runner.invoke(backfill_launchpad_maintainers.main, ["--dry-run"])

    assert result.exit_code == 0, result.output

    with Session(engine) as session:
        issue = session.execute(
            select(Issue).where(Issue.author == "~cmatsuoka")
        ).scalar_one()
    assert issue.author_is_maintainer is False
