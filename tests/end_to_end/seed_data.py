"""Deterministic seed data for E2E tests.

This module generates a fixed dataset that exercises every page and chart
in the craft-dashboard application.  It produces SQL INSERT statements that
can be executed against a live PostgreSQL database via the ``/e2e/seed``
endpoint (only available when ``CRAFT_DASHBOARD_E2E=1``).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
PROJECTS = [
    {
        "name": "snapcraft",
        "category": "application",
        "github_org": "canonical",
        "display_order": 10,
    },
    {
        "name": "charmcraft",
        "category": "application",
        "github_org": "canonical",
        "display_order": 20,
    },
    {
        "name": "rockcraft",
        "category": "application",
        "github_org": "canonical",
        "display_order": 30,
    },
    {
        "name": "craft-parts",
        "category": "library",
        "github_org": "canonical",
        "display_order": 40,
    },
    {
        "name": "all-projects",
        "category": "aggregate",
        "github_org": "canonical",
        "display_order": -1,
    },
]


def _date_range(start: date, end: date) -> list[date]:
    """Return a list of dates from start to end inclusive."""
    days = (end - start).days + 1
    return [start + timedelta(days=i) for i in range(days)]


# ---------------------------------------------------------------------------
# Snapshots - one per day for ~60 days so trends charts have data to render
# ---------------------------------------------------------------------------
def _make_snapshots(project_id: int, project_name: str) -> list[dict]:
    """Generate snapshot rows for a project.

    Each project gets a distinct data profile so view-switching in charts
    produces visibly different curves.
    """
    end = date(2024, 7, 1)
    start = date(2024, 5, 1)
    dates = _date_range(start, end)

    multiplier = {
        "snapcraft": 5,
        "charmcraft": 3,
        "rockcraft": 2,
        "craft-parts": 1,
        "all-projects": 10,
    }.get(project_name, 1)

    snapshots = []
    for i, d in enumerate(dates):
        base_issues = 20 * multiplier + i
        base_prs = 5 * multiplier + i // 2

        # Distribute issues across author groups
        external_issues = int(base_issues * 0.6)
        bots_issues = int(base_issues * 0.1)
        internal_issues = base_issues - external_issues - bots_issues

        external_prs = int(base_prs * 0.5)
        bots_prs = int(base_prs * 0.15)
        internal_prs = base_prs - external_prs - bots_prs

        # Closed counts (daily increments)
        closed_issues = max(1, i // 5)
        closed_prs = max(1, i // 7)
        closed_ext_issues = max(1, closed_issues // 2)
        closed_int_issues = closed_issues - closed_ext_issues
        closed_ext_prs = max(1, closed_prs // 2)
        closed_int_prs = closed_prs - closed_ext_prs
        closed_bots_issues = max(0, i // 15)
        closed_bots_prs = max(0, i // 20)

        snapshots.append(
            {
                "project_id": project_id,
                "snapshot_date": d.isoformat(),
                "open_issues": base_issues,
                "open_prs": base_prs,
                "open_issues_external": external_issues,
                "open_issues_internal": internal_issues,
                "open_prs_external": external_prs,
                "open_prs_internal": internal_prs,
                "open_issues_bots": bots_issues,
                "open_prs_bots": bots_prs,
                "open_bugs": int(base_issues * 0.3),
                "median_issue_age": 100 + i * 2 + multiplier * 10,
                "median_pr_age": 30 + i + multiplier * 5,
                "nm_median_issue_age": 80 + i * 2 + multiplier * 8,
                "nm_median_pr_age": 25 + i + multiplier * 4,
                "median_issue_age_internal": 150 + i * 3 + multiplier * 12,
                "median_pr_age_internal": 45 + i * 2 + multiplier * 7,
                "median_issue_age_bots": 10 + i,
                "median_pr_age_bots": 5 + i // 2,
                "median_age": 70 + i + multiplier * 8,
                "nm_median_age": 55 + i + multiplier * 6,
                "median_age_internal": 100 + i * 2 + multiplier * 10,
                "median_age_bots": 8 + i,
                "closed_issues": closed_issues,
                "closed_prs": closed_prs,
                "closed_issues_external": closed_ext_issues,
                "closed_issues_internal": closed_int_issues,
                "closed_prs_external": closed_ext_prs,
                "closed_prs_internal": closed_int_prs,
                "closed_issues_bots": closed_bots_issues,
                "closed_prs_bots": closed_bots_prs,
            }
        )
    return snapshots


# ---------------------------------------------------------------------------
# Issues - a handful of open/closed issues per project
# ---------------------------------------------------------------------------
def _make_issues(project_id: int, project_name: str) -> list[dict]:
    """Generate issue rows for a project."""
    now = datetime(2024, 7, 1, 12, 0, 0, tzinfo=UTC)
    issues = []
    base = 1000 * PROJECTS.index(next(p for p in PROJECTS if p["name"] == project_name))

    configs = [
        # state, author_maintainer, author_bot, issue_type, age_days
        ("open", True, False, "issue", 30),
        ("open", True, False, "issue", 90),
        ("open", False, False, "issue", 180),
        ("open", False, False, "pull_request", 15),
        ("open", False, True, "issue", 5),
        ("open", False, True, "pull_request", 2),
        ("closed", True, False, "issue", 60),
        ("closed", False, False, "pull_request", 45),
    ]

    for j, (state, is_maint, is_bot, itype, age) in enumerate(configs):
        created = now - timedelta(days=age)
        closed = (now - timedelta(days=age // 3)) if state == "closed" else None
        issues.append(
            {
                "project_id": project_id,
                "source": "github",
                "external_id": str(base + j + 1),
                "issue_type": itype,
                "title": f"[{project_name}] Test {itype} #{j + 1} ({state})",
                "body": f"Test body for {project_name} {itype} #{j + 1}",
                "state": state,
                "author": "test-maintainer"
                if is_maint
                else ("renovate[bot]" if is_bot else "community-user"),
                "author_is_maintainer": is_maint,
                "author_is_bot": is_bot,
                "labels": json.dumps(["bug"] if j % 3 == 0 else ["enhancement"]),
                "created_at": created.isoformat(),
                "updated_at": (closed or now).isoformat(),
                "closed_at": closed.isoformat() if closed else None,
                "url": f"https://github.com/canonical/{project_name}/issues/{base + j + 1}",
                "metadata": json.dumps({}),
                "comments": json.dumps([]),
                "last_fetched_at": now.isoformat(),
            }
        )
    return issues


# ---------------------------------------------------------------------------
# Releases - a few per application project
# ---------------------------------------------------------------------------
def _make_releases(project_id: int, project_name: str) -> list[dict]:
    """Generate release rows for application projects."""
    if project_name in ("craft-parts", "all-projects"):
        return []

    now = datetime(2024, 7, 1, 12, 0, 0, tzinfo=UTC)
    releases = []
    versions = [
        ("8.0.0", "main", 30, False, 150),
        ("7.5.1", "7.x/stable", 60, True, 5),
        ("7.5.0", "7.x/candidate", 90, False, 80),
    ]
    for ver, branch, days_ago, is_hotfix, commits in versions:
        releases.append(
            {
                "project_id": project_id,
                "version": ver,
                "branch": branch,
                "released_at": (now - timedelta(days=days_ago)).isoformat(),
                "is_hotfix": is_hotfix,
                "metadata": json.dumps({"commits_since_tag": commits}),
            }
        )
    return releases


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
def _make_dependencies(project_id: int, project_name: str) -> list[dict]:
    """Generate dependency rows for a project."""
    if project_name == "all-projects":
        return []

    now = datetime(2024, 7, 1, 12, 0, 0, tzinfo=UTC)
    deps = [
        ("craft-parts", ">=1.0,<2.0", "1.5.0", "1.6.0", True),
        ("craft-cli", ">=2.0", "2.1.0", "2.1.0", False),
        ("pydantic", ">=2.0,<3.0", "2.5.0", "2.9.0", True),
    ]
    return [
        {
            "project_id": project_id,
            "branch": "main",
            "dependency_name": name,
            "version_spec": spec,
            "source_file": "pyproject.toml",
            "fetched_at": now.isoformat(),
            "installed_version": installed,
            "latest_version": latest,
            "is_outdated": outdated,
        }
        for name, spec, installed, latest, outdated in deps
    ]


# ---------------------------------------------------------------------------
# LLM Evaluations - one per issue for triage page testing
# ---------------------------------------------------------------------------
_ACTIONS = [
    "close_stale",
    "needs_triage",
    "keep_open",
    "needs_review",
    "close_duplicate",
    "close_not_a_bug",
    "close_not_mergeable",
]

_ACTION_REASONS = [
    "No activity for over 6 months, likely abandoned.",
    "New issue needs initial assessment by a maintainer.",
    "Active discussion and recent commits, keep monitoring.",
    "PR has approvals but CI is failing, needs author attention.",
    "Very similar to issue #42, likely a duplicate report.",
    "References an API that was removed in v3.0.",
    "This is expected behavior, not a bug.",
    "Makes a breaking change that maintainers have rejected.",
]


def _make_llm_evaluations(
    issue_id_start: int,
    issue_count: int,
    project_name: str,
) -> list[dict]:
    """Generate LLM evaluation rows for seeded issues."""
    evals = []
    now = datetime(2024, 7, 1, 12, 0, 0, tzinfo=UTC)

    for j in range(issue_count):
        issue_id = issue_id_start + j
        # Deterministic scores based on index
        is_pr = j in (3, 5, 7)  # matches _make_issues PR indices
        staleness = min(100, (j + 1) * 15)
        complexity = min(100, 20 + j * 10)

        scores: dict = {
            "staleness": staleness,
            "complexity": complexity,
        }
        if is_pr:
            scores["confidence"] = min(100, 30 + j * 15)
        else:
            scores["support_request"] = min(100, j * 18)
            scores["confidence"] = min(100, 40 + j * 12)
        action_idx = j % len(_ACTIONS)
        evals.append(
            {
                "issue_id": issue_id,
                "model_name": "test-model",
                "summary": f"Test summary for {project_name} issue #{j + 1}.",
                "suggested_action": _ACTIONS[action_idx],
                "suggested_action_reason": _ACTION_REASONS[action_idx],
                "scores": json.dumps(scores),
                "tokens_used": 100 + j * 10,
                "evaluated_at": now.isoformat(),
                "issue_data_hash": f"seed-hash-{issue_id}",
                "latest": True,
            }
        )
    return evals


# ---------------------------------------------------------------------------
# Forum activity - topics spread over the last several months so the
# Engagement page's trend charts have real month-to-month variation.
# ---------------------------------------------------------------------------
FORUMS = ["snapcraft", "charmcraft", "rockcraft"]
FORUM_CATEGORIES = ["bugs", "questions", "features", "docs"]


def _make_forum_topics(forum_name: str) -> list[dict]:
    """Generate forum topic rows spanning the last 6 months for a forum."""
    now = datetime(2024, 7, 1, 12, 0, 0, tzinfo=UTC)
    topics = []
    topic_id = 0
    # 6 months, a handful of topics per month, cycling through categories so
    # each category has a distinct (and differing) monthly series.
    for month_offset in range(6):
        created = now - timedelta(days=30 * month_offset + 3)
        for i in range(3):
            topic_id += 1
            category = FORUM_CATEGORIES[(month_offset + i) % len(FORUM_CATEGORIES)]
            posts_count = 2 + i + month_offset
            topics.append(
                {
                    "forum": forum_name,
                    "category": category,
                    "external_id": topic_id,
                    "title": f"[{forum_name}] Test topic {topic_id}",
                    "posts_count": posts_count,
                    "like_count": i,
                    "created_at": (created - timedelta(days=i)).isoformat(),
                    "last_posted_at": created.isoformat(),
                    "url": f"https://{forum_name}.example.com/t/{topic_id}",
                    "last_fetched_at": now.isoformat(),
                }
            )
    return topics


def _make_forum_backfill_state(forum_name: str) -> dict:
    """Generate a forum_backfill_state row caching the forum's categories."""
    now = datetime(2024, 7, 1, 12, 0, 0, tzinfo=UTC)
    return {
        "forum": forum_name,
        "categories_cache": json.dumps(sorted(FORUM_CATEGORIES)),
        "categories_cached_at": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def generate_seed_sql() -> str:
    """Return SQL statements that populate the database with test data.

    The SQL is idempotent: it deletes existing rows before inserting.
    """
    stmts: list[str] = []
    stmts.append("DELETE FROM dependencies;")
    stmts.append("DELETE FROM releases;")
    stmts.append("DELETE FROM llm_evaluations;")
    stmts.append("DELETE FROM issues;")
    stmts.append("DELETE FROM snapshots;")
    stmts.append("DELETE FROM refresh_schedule;")
    stmts.append("DELETE FROM projects;")
    stmts.append("DELETE FROM forum_topics;")
    stmts.append("DELETE FROM forum_backfill_state;")

    for pid, proj in enumerate(PROJECTS, start=1):
        stmts.append(
            f"INSERT INTO projects (id, name, category, github_org, display_order) "
            f"VALUES ({pid}, '{proj['name']}', '{proj['category']}', "
            f"'{proj['github_org']}', {proj['display_order']});"
        )

    for pid, proj in enumerate(PROJECTS, start=1):
        for snap in _make_snapshots(pid, proj["name"]):
            cols = ", ".join(snap.keys())
            vals = ", ".join(
                f"'{v}'" if isinstance(v, str) else str(v) for v in snap.values()
            )
            stmts.append(f"INSERT INTO snapshots ({cols}) VALUES ({vals});")

    for pid, proj in enumerate(PROJECTS, start=1):
        for issue in _make_issues(pid, proj["name"]):
            cols = ", ".join(issue.keys())
            vals = []
            for v in issue.values():
                if v is None:
                    vals.append("NULL")
                elif isinstance(v, bool):
                    vals.append("true" if v else "false")
                elif isinstance(v, (int, float)):
                    vals.append(str(v))
                else:
                    vals.append(f"'{str(v).replace(chr(39), chr(39) + chr(39))}'")
            stmts.append(f"INSERT INTO issues ({cols}) VALUES ({', '.join(vals)});")

    for pid, proj in enumerate(PROJECTS, start=1):
        for rel in _make_releases(pid, proj["name"]):
            cols = ", ".join(rel.keys())
            vals = []
            for v in rel.values():
                if v is None:
                    vals.append("NULL")
                elif isinstance(v, bool):
                    vals.append("true" if v else "false")
                elif isinstance(v, (int, float)):
                    vals.append(str(v))
                else:
                    vals.append(f"'{str(v).replace(chr(39), chr(39) + chr(39))}'")
            stmts.append(f"INSERT INTO releases ({cols}) VALUES ({', '.join(vals)});")

    for pid, proj in enumerate(PROJECTS, start=1):
        for dep in _make_dependencies(pid, proj["name"]):
            cols = ", ".join(dep.keys())
            vals = []
            for v in dep.values():
                if v is None:
                    vals.append("NULL")
                elif isinstance(v, bool):
                    vals.append("true" if v else "false")
                elif isinstance(v, (int, float)):
                    vals.append(str(v))
                else:
                    vals.append(f"'{str(v).replace(chr(39), chr(39) + chr(39))}'")
            stmts.append(
                f"INSERT INTO dependencies ({cols}) VALUES ({', '.join(vals)});"
            )

    # Insert LLM evaluations for each project's issues
    issue_id_counter = 1
    for pid, proj in enumerate(PROJECTS, start=1):
        issue_count = len(_make_issues(pid, proj["name"]))
        if issue_count > 0:
            for ev in _make_llm_evaluations(
                issue_id_counter, issue_count, proj["name"]
            ):
                cols = ", ".join(ev.keys())
                vals = []
                for v in ev.values():
                    if v is None:
                        vals.append("NULL")
                    elif isinstance(v, bool):
                        vals.append("true" if v else "false")
                    elif isinstance(v, (int, float)):
                        vals.append(str(v))
                    else:
                        vals.append(f"'{str(v).replace(chr(39), chr(39) + chr(39))}'")
                stmts.append(
                    f"INSERT INTO llm_evaluations ({cols}) VALUES ({', '.join(vals)});"
                )
        issue_id_counter += issue_count

    for forum_name in FORUMS:
        for topic in _make_forum_topics(forum_name):
            cols = ", ".join(topic.keys())
            vals = []
            for v in topic.values():
                if v is None:
                    vals.append("NULL")
                elif isinstance(v, bool):
                    vals.append("true" if v else "false")
                elif isinstance(v, (int, float)):
                    vals.append(str(v))
                else:
                    vals.append(f"'{str(v).replace(chr(39), chr(39) + chr(39))}'")
            stmts.append(
                f"INSERT INTO forum_topics ({cols}) VALUES ({', '.join(vals)});"
            )

        for state_row in [_make_forum_backfill_state(forum_name)]:
            cols = ", ".join(state_row.keys())
            vals = []
            for v in state_row.values():
                if v is None:
                    vals.append("NULL")
                elif isinstance(v, bool):
                    vals.append("true" if v else "false")
                elif isinstance(v, (int, float)):
                    vals.append(str(v))
                else:
                    vals.append(f"'{str(v).replace(chr(39), chr(39) + chr(39))}'")
            stmts.append(
                f"INSERT INTO forum_backfill_state ({cols}) VALUES ({', '.join(vals)});"
            )

    # Reset sequence counters
    stmts.extend(
        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
        f"COALESCE((SELECT MAX(id) FROM {table}), 1));"
        for table in (
            "projects",
            "snapshots",
            "issues",
            "releases",
            "dependencies",
            "llm_evaluations",
            "forum_topics",
            "forum_backfill_state",
        )
    )

    return "\n".join(stmts)
