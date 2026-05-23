"""Stats routes for dependencies, releases, and trends."""

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.dependency import Dependency
from craft_dashboard.models.project import Project
from craft_dashboard.models.release import Release
from craft_dashboard.models.snapshot import Snapshot

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/stats")

_SCALAR_KEYS = [
    "open_issues",
    "open_prs",
    "open_issues_external",
    "open_prs_external",
    "open_issues_bots",
    "open_prs_bots",
    "open",
    "open_external",
    "open_internal",
    "open_bots",
    "closed_issues",
    "closed_prs",
    "closed_issues_external",
    "closed_prs_external",
    "closed_issues_bots",
    "closed_prs_bots",
    "closed",
    "closed_external",
    "closed_internal",
    "closed_bots",
    "open_bugs",
]
_MEDIAN_AGE_KEYS = [
    "median_issue_age",
    "median_pr_age",
    "nm_median_issue_age",
    "nm_median_pr_age",
    "median_issue_age_internal",
    "median_pr_age_internal",
    "median_issue_age_bots",
    "median_pr_age_bots",
    "median_age",
    "nm_median_age",
    "median_age_internal",
    "median_age_bots",
]


@router.get("", response_class=RedirectResponse)
async def stats_index() -> RedirectResponse:
    """Redirect /stats to /stats/trends."""
    return RedirectResponse(url="/stats/trends", status_code=302)


@router.get("/dependencies", response_class=HTMLResponse)
async def dependencies_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the dependency usage table."""
    templates: Jinja2Templates = request.app.state.templates

    result = await session.execute(
        select(
            Dependency.dependency_name,
            Dependency.branch,
            Dependency.version_spec,
            Dependency.installed_version,
            Project.name.label("project_name"),
        )
        .join(Project, Dependency.project_id == Project.id)
        .order_by(Project.display_order, Dependency.branch, Dependency.dependency_name)
    )

    dependencies = [
        {
            "project_name": row.project_name,
            "branch": row.branch,
            "dependency_name": row.dependency_name,
            "version_spec": row.version_spec,
            "installed_version": row.installed_version,
        }
        for row in result
    ]

    return templates.TemplateResponse(
        request,
        "stats/dependencies.html",
        {"dependencies": dependencies},
    )


@router.get("/dependencies/data")
async def dependencies_data(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return dependency data as JSON for the chart."""
    config = request.app.state.config
    libs = sorted(config.craft_libraries)

    result = await session.execute(
        select(
            Project.name.label("project_name"),
            Dependency.branch,
            Dependency.dependency_name,
            Dependency.version_spec,
            Dependency.installed_version,
            Dependency.latest_version,
            Dependency.series,
            Dependency.is_outdated,
        )
        .join(Project, Dependency.project_id == Project.id)
        .where(Project.category == "application")
        .where(Dependency.dependency_name.in_(libs))
        .order_by(Project.name, Dependency.branch, Dependency.dependency_name)
    )

    apps: dict = {}
    for row in result:
        if row.dependency_name not in libs:
            continue

        key = f"{row.project_name}/{row.branch}"
        if key not in apps:
            apps[key] = {}
        if row.installed_version is not None:
            apps[key][row.dependency_name] = {
                "version": row.installed_version,
                "latest": row.latest_version,
                "series": row.series,
                "outdated": row.is_outdated,
            }
        else:
            apps[key][row.dependency_name] = {"version_spec": row.version_spec or "any"}

    return {"libs": libs, "apps": apps}


@router.get("/releases", response_class=HTMLResponse)
async def releases_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the releases table showing the latest release per project+branch."""
    templates: Jinja2Templates = request.app.state.templates

    from datetime import UTC, datetime  # noqa: PLC0415 — deferred import

    result = await session.execute(
        select(
            Project.name.label("project_name"),
            Release.branch,
            Release.version,
            Release.released_at,
            Release.metadata_,
        )
        .join(Project, Release.project_id == Project.id)
        .where(Project.category == "application")
        .order_by(Project.display_order, Release.branch)
    )

    releases = []
    for row in result:
        days_ago = None
        if row.released_at:
            released = (
                row.released_at.replace(tzinfo=UTC)
                if row.released_at.tzinfo is None
                else row.released_at
            )
            days_ago = (datetime.now(tz=UTC) - released).days
        commits_since_tag = None
        if row.metadata_:
            commits_since_tag = row.metadata_.get("commits_since_tag")
        releases.append(
            {
                "project_name": row.project_name,
                "branch": row.branch or "main",
                "version": row.version,
                "released_at": row.released_at,
                "days_ago": days_ago,
                "commits_since_tag": commits_since_tag,
            }
        )

    return templates.TemplateResponse(
        request,
        "stats/releases.html",
        {"releases": releases},
    )


@router.get("/trends", response_class=HTMLResponse)
async def trends_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the trends page with Chart.js graphs."""
    templates: Jinja2Templates = request.app.state.templates

    project_result = await session.execute(
        select(Project.name).order_by(Project.display_order)
    )
    project_names = [row.name for row in project_result]

    return templates.TemplateResponse(
        request,
        "stats/trends.html",
        {"project_names": project_names},
    )


def _build_all_projects_aggregate(  # noqa: PLR0912
    projects: dict[str, dict],
    db_medians: dict[str, list] | None = None,
) -> dict[str, list]:
    """Build the all-projects time-aligned aggregate.

    Scalar keys (open_issues, closed_prs, etc.) are summed across projects.
    Median keys use pre-computed true cross-project medians from the DB when
    available; otherwise fall back to a weighted average.
    """
    proj_by_date: dict[str, dict[str, int]] = {}
    for name, data in projects.items():
        proj_by_date[name] = {d: i for i, d in enumerate(data["dates"])}

    all_dates_set: set[str] = set()
    for data in projects.values():
        all_dates_set.update(data["dates"])
    all_dates_sorted = sorted(all_dates_set)

    # Build a lookup for DB median values by date
    db_median_by_date: dict[str, dict[str, int]] | None = None
    if db_medians and db_medians.get("dates"):
        db_median_by_date = {}
        for i, d in enumerate(db_medians["dates"]):
            db_median_by_date[d] = {mk: db_medians[mk][i] for mk in _MEDIAN_AGE_KEYS}

    all_projects: dict[str, list] = {
        "dates": all_dates_sorted,
        **{k: [] for k in _SCALAR_KEYS},
        **{k: [] for k in _MEDIAN_AGE_KEYS},
    }

    for d in all_dates_sorted:
        for k in _SCALAR_KEYS:
            total = 0
            for name, data in projects.items():
                idx = proj_by_date[name].get(d)
                if idx is not None:
                    total += data[k][idx]
            all_projects[k].append(total)

        if db_median_by_date and d in db_median_by_date:
            for mk in _MEDIAN_AGE_KEYS:
                all_projects[mk].append(db_median_by_date[d][mk])
        else:
            # Fallback: weighted average by open item count
            for mk in _MEDIAN_AGE_KEYS:
                weighted_sum = 0
                total_weight = 0
                for name, data in projects.items():
                    idx = proj_by_date[name].get(d)
                    if idx is not None and data[mk][idx] > 0:
                        weight = max(data["open_issues"][idx] + data["open_prs"][idx], 1)
                        weighted_sum += data[mk][idx] * weight
                        total_weight += weight
                all_projects[mk].append(
                    int(weighted_sum / total_weight) if total_weight > 0 else 0
                )

    return all_projects


def _build_snapshot_dict(projects: dict[str, dict]) -> dict[str, dict]:
    """Build latest snapshot values for each project and the all-projects rollup."""
    snapshot: dict[str, dict] = {}
    for name, data in projects.items():
        if name == "all-projects" or not data["dates"]:
            continue
        idx = -1  # Last day

        # Compute year-ago index (approximately 365 days back)
        n = len(data["dates"])
        year_start_idx = max(0, n - 365)

        # Sum closed issues/PRs over last year
        closed_issues_year = sum(data["closed_issues"][year_start_idx:])
        closed_prs_year = sum(data["closed_prs"][year_start_idx:])
        nm_closed_issues_year = sum(data["closed_issues_external"][year_start_idx:])
        nm_closed_prs_year = sum(data["closed_prs_external"][year_start_idx:])

        snapshot[name] = {
            "open_issues": data["open_issues"][idx],
            "open_prs": data["open_prs"][idx],
            "nm_open_issues": data["open_issues_external"][idx],
            "nm_open_prs": data["open_prs_external"][idx],
            "bots_open_issues": data["open_issues_bots"][idx],
            "bots_open_prs": data["open_prs_bots"][idx],
            "internal_open_issues": data["open_issues"][idx]
            - data["open_issues_external"][idx]
            - data["open_issues_bots"][idx],
            "internal_open_prs": data["open_prs"][idx]
            - data["open_prs_external"][idx]
            - data["open_prs_bots"][idx],
            "median_issue_age": data["median_issue_age"][idx],
            "median_pr_age": data["median_pr_age"][idx],
            "nm_median_issue_age": data["nm_median_issue_age"][idx],
            "nm_median_pr_age": data["nm_median_pr_age"][idx],
            "median_issue_age_internal": data["median_issue_age_internal"][idx],
            "median_pr_age_internal": data["median_pr_age_internal"][idx],
            "median_issue_age_bots": data["median_issue_age_bots"][idx],
            "median_pr_age_bots": data["median_pr_age_bots"][idx],
            "closed_issues_year": closed_issues_year,
            "closed_prs_year": closed_prs_year,
            "nm_closed_issues_year": nm_closed_issues_year,
            "nm_closed_prs_year": nm_closed_prs_year,
            "bots_closed_issues_year": sum(
                data["closed_issues_bots"][year_start_idx:]
            ),
            "bots_closed_prs_year": sum(data["closed_prs_bots"][year_start_idx:]),
            "internal_closed_issues_year": closed_issues_year
            - nm_closed_issues_year
            - sum(data["closed_issues_bots"][year_start_idx:]),
            "internal_closed_prs_year": closed_prs_year
            - nm_closed_prs_year
            - sum(data["closed_prs_bots"][year_start_idx:]),
        }

    scalar_snap_keys = [
        "open_issues",
        "open_prs",
        "nm_open_issues",
        "nm_open_prs",
        "bots_open_issues",
        "bots_open_prs",
        "internal_open_issues",
        "internal_open_prs",
        "closed_issues_year",
        "closed_prs_year",
        "nm_closed_issues_year",
        "nm_closed_prs_year",
        "bots_closed_issues_year",
        "bots_closed_prs_year",
        "internal_closed_issues_year",
        "internal_closed_prs_year",
    ]
    median_snap_keys = [
        "median_issue_age",
        "median_pr_age",
        "nm_median_issue_age",
        "nm_median_pr_age",
        "median_issue_age_internal",
        "median_pr_age_internal",
        "median_issue_age_bots",
        "median_pr_age_bots",
    ]
    ap_snap: dict[str, int] = dict.fromkeys(scalar_snap_keys, 0)
    ap_snap.update(dict.fromkeys(median_snap_keys, 0))
    median_counts: dict[str, int] = dict.fromkeys(median_snap_keys, 0)

    for proj_snap in snapshot.values():
        for k in scalar_snap_keys:
            ap_snap[k] += proj_snap.get(k, 0)
        for mk in median_snap_keys:
            val = proj_snap.get(mk, 0)
            if val > 0:
                ap_snap[mk] += val
                median_counts[mk] += 1

    for mk in median_snap_keys:
        if median_counts[mk] > 0:
            ap_snap[mk] = ap_snap[mk] // median_counts[mk]

    snapshot["all-projects"] = ap_snap
    return snapshot


@router.get("/trends/all-data", response_class=JSONResponse)
async def trends_all_data(
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Return enriched snapshot trend data for all projects as JSON."""
    result = await session.execute(
        select(
            Project.name.label("project_name"),
            Snapshot.snapshot_date,
            Snapshot.open_issues,
            Snapshot.open_prs,
            Snapshot.open_issues_external,
            Snapshot.open_issues_internal,
            Snapshot.open_prs_external,
            Snapshot.open_prs_internal,
            Snapshot.open_issues_bots,
            Snapshot.open_prs_bots,
            Snapshot.open_bugs,
            Snapshot.median_issue_age,
            Snapshot.median_pr_age,
            Snapshot.nm_median_issue_age,
            Snapshot.nm_median_pr_age,
            Snapshot.median_issue_age_internal,
            Snapshot.median_pr_age_internal,
            Snapshot.median_issue_age_bots,
            Snapshot.median_pr_age_bots,
            Snapshot.median_age,
            Snapshot.nm_median_age,
            Snapshot.median_age_internal,
            Snapshot.median_age_bots,
            Snapshot.closed_issues,
            Snapshot.closed_prs,
            Snapshot.closed_issues_external,
            Snapshot.closed_issues_internal,
            Snapshot.closed_prs_external,
            Snapshot.closed_prs_internal,
            Snapshot.closed_issues_bots,
            Snapshot.closed_prs_bots,
        )
        .join(Project, Snapshot.project_id == Project.id)
        .order_by(Project.display_order, Snapshot.snapshot_date)
    )

    projects: dict[str, dict] = {}
    for row in result:
        name = row.project_name
        if name not in projects:
            projects[name] = {
                "dates": [],
                "open_issues": [],
                "open_prs": [],
                "open_issues_external": [],
                "open_prs_external": [],
                "open_issues_bots": [],
                "open_prs_bots": [],
                "open": [],
                "open_external": [],
                "open_internal": [],
                "open_bots": [],
                "median_issue_age": [],
                "median_pr_age": [],
                "nm_median_issue_age": [],
                "nm_median_pr_age": [],
                "median_issue_age_internal": [],
                "median_pr_age_internal": [],
                "median_issue_age_bots": [],
                "median_pr_age_bots": [],
                "median_age": [],
                "nm_median_age": [],
                "median_age_internal": [],
                "median_age_bots": [],
                "closed_issues": [],
                "closed_prs": [],
                "closed_issues_external": [],
                "closed_prs_external": [],
                "closed_issues_bots": [],
                "closed_prs_bots": [],
                "closed": [],
                "closed_external": [],
                "closed_internal": [],
                "closed_bots": [],
                "open_bugs": [],
            }
        projects[name]["dates"].append(row.snapshot_date.isoformat())
        projects[name]["open_issues"].append(row.open_issues)
        projects[name]["open_prs"].append(row.open_prs)
        projects[name]["open_issues_external"].append(row.open_issues_external)
        projects[name]["open_prs_external"].append(row.open_prs_external)
        projects[name]["open_issues_bots"].append(row.open_issues_bots)
        projects[name]["open_prs_bots"].append(row.open_prs_bots)
        projects[name]["open"].append(row.open_issues + row.open_prs)
        projects[name]["open_external"].append(
            row.open_issues_external + row.open_prs_external
        )
        projects[name]["open_internal"].append(
            row.open_issues_internal + row.open_prs_internal
        )
        projects[name]["open_bots"].append(row.open_issues_bots + row.open_prs_bots)
        projects[name]["median_issue_age"].append(row.median_issue_age)
        projects[name]["median_pr_age"].append(row.median_pr_age)
        projects[name]["nm_median_issue_age"].append(row.nm_median_issue_age)
        projects[name]["nm_median_pr_age"].append(row.nm_median_pr_age)
        projects[name]["median_issue_age_internal"].append(
            row.median_issue_age_internal
        )
        projects[name]["median_pr_age_internal"].append(row.median_pr_age_internal)
        projects[name]["median_issue_age_bots"].append(row.median_issue_age_bots)
        projects[name]["median_pr_age_bots"].append(row.median_pr_age_bots)
        projects[name]["median_age"].append(row.median_age)
        projects[name]["nm_median_age"].append(row.nm_median_age)
        projects[name]["median_age_internal"].append(row.median_age_internal)
        projects[name]["median_age_bots"].append(row.median_age_bots)
        projects[name]["closed_issues"].append(row.closed_issues)
        projects[name]["closed_prs"].append(row.closed_prs)
        projects[name]["closed_issues_external"].append(row.closed_issues_external)
        projects[name]["closed_prs_external"].append(row.closed_prs_external)
        projects[name]["closed_issues_bots"].append(row.closed_issues_bots)
        projects[name]["closed_prs_bots"].append(row.closed_prs_bots)
        projects[name]["closed"].append(row.closed_issues + row.closed_prs)
        projects[name]["closed_external"].append(
            row.closed_issues_external + row.closed_prs_external
        )
        projects[name]["closed_internal"].append(
            row.closed_issues_internal + row.closed_prs_internal
        )
        projects[name]["closed_bots"].append(
            row.closed_issues_bots + row.closed_prs_bots
        )
        projects[name]["open_bugs"].append(row.open_bugs)

    project_order = list(projects.keys())

    # If the DB contains pre-computed all-projects snapshots, pop them and
    # use their true cross-project medians in the aggregate.
    db_all_projects = projects.pop("all-projects", None)
    project_order = [p for p in project_order if p != "all-projects"]

    if projects:
        projects["all-projects"] = _build_all_projects_aggregate(
            projects, db_medians=db_all_projects
        )

    snapshot = _build_snapshot_dict(projects)

    return JSONResponse(
        {"projects": projects, "order": project_order, "snapshot": snapshot}
    )


async def _get_trend_chart_data(
    session: AsyncSession, project: str
) -> dict | None:
    """Fetch snapshot trend data for a project. Returns None if not found."""
    exists = await session.scalar(
        select(func.count()).select_from(Project).where(Project.name == project)
    )
    if not exists:
        return None

    result = await session.execute(
        select(
            Snapshot.snapshot_date,
            Snapshot.open_issues,
            Snapshot.open_prs,
            Snapshot.open_bugs,
        )
        .join(Project, Snapshot.project_id == Project.id)
        .where(Project.name == project)
        .order_by(Snapshot.snapshot_date)
    )

    dates: list[str] = []
    open_issues: list[int] = []
    open_prs: list[int] = []
    open_bugs: list[int] = []

    for row in result:
        dates.append(row.snapshot_date.isoformat())
        open_issues.append(row.open_issues)
        open_prs.append(row.open_prs)
        open_bugs.append(row.open_bugs)

    return {
        "labels": dates,
        "datasets": [
            {"label": "Open Issues", "data": open_issues, "borderColor": "#4e79a7"},
            {"label": "Open PRs", "data": open_prs, "borderColor": "#f28e2b"},
            {"label": "Open Bugs", "data": open_bugs, "borderColor": "#e15759"},
        ],
    }


@router.get("/trends/data", response_class=JSONResponse)
async def trends_data(
    session: AsyncSession = Depends(get_db_session),
    project: str = Query(...),
) -> JSONResponse:
    """Return trend data as JSON for Chart.js (API endpoint)."""
    data = await _get_trend_chart_data(session, project)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    return JSONResponse(data)


@router.get("/trends/chart", response_class=HTMLResponse)
async def trends_chart_partial(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    project: str = Query(...),
) -> HTMLResponse:
    """Return an HTML partial that renders the trend chart for a given project.

    Called by HTMX when the project selector changes or the page loads.
    Embeds chart data as inline JS so Chart.js can render without a second request.
    """
    templates: Jinja2Templates = request.app.state.templates
    import json  # noqa: PLC0415 — deferred import

    chart_data = await _get_trend_chart_data(session, project)
    if chart_data is None:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

    return templates.TemplateResponse(
        request,
        "stats/partials/trend_chart.html",
        {"chart_data_json": json.dumps(chart_data), "project": project},
    )
