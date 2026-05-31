# Plan 4: Web Dashboard & Frontend

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the FastAPI routes and HTMX-powered frontend for the craft-dashboard. This includes the main dashboard overview, issue/PR triage views with filtering, legacy stats views (dependencies, releases, trends), and admin authentication.

**Architecture:** FastAPI route modules return Jinja2-rendered HTML. HTMX handles dynamic updates (filtering, sorting, pagination) by swapping HTML fragments from dedicated partial endpoints. Chart.js renders trend graphs client-side from JSON API endpoints. Auth uses a bearer token for admin endpoints.

**Tech Stack:** FastAPI, Jinja2, HTMX, Vanilla framework (Canonical), Chart.js

> **Existing code to read before implementing:** `html/js/issues.js` and `html/js/index.js` (Chart.js config and filter logic to port), `html/index.html` and `html/issues.html` (Vanilla framework class usage and layout patterns), `html/css/custom.css` (existing customizations to carry forward).

**Depends on:** Plans 1 and 2

---

### Task 1: Auth Dependency

**Files:**
- Create: `craft_dashboard/auth.py`
- Test: `tests/unit/test_auth.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_auth.py`:
```python
"""Tests for authentication."""

import pytest
from fastapi import HTTPException

from craft_dashboard.auth import verify_admin_token


class TestVerifyAdminToken:
    """Tests for verify_admin_token."""

    def test_valid_token(self) -> None:
        """Valid token passes without error."""
        verify_admin_token(
            token="Bearer correct-token", admin_token="correct-token"
        )

    def test_invalid_token_raises(self) -> None:
        """Invalid token raises HTTPException 401."""
        with pytest.raises(HTTPException) as exc_info:
            verify_admin_token(
                token="Bearer wrong-token", admin_token="correct-token"
            )
        assert exc_info.value.status_code == 401

    def test_missing_bearer_prefix_raises(self) -> None:
        """Token without 'Bearer ' prefix raises HTTPException 401."""
        with pytest.raises(HTTPException) as exc_info:
            verify_admin_token(
                token="correct-token", admin_token="correct-token"
            )
        assert exc_info.value.status_code == 401

    def test_empty_admin_token_raises(self) -> None:
        """Empty admin token always rejects (misconfiguration guard)."""
        with pytest.raises(HTTPException) as exc_info:
            verify_admin_token(
                token="Bearer anything", admin_token=""
            )
        assert exc_info.value.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/auth.py`:
```python
"""Authentication helpers for admin endpoints."""

from fastapi import HTTPException, status


def verify_admin_token(token: str, admin_token: str) -> None:
    """Verify that the provided bearer token matches the admin token.

    Args:
        token: The Authorization header value (e.g., 'Bearer <token>').
        admin_token: The expected admin token from settings.

    Raises:
        HTTPException: If the token is invalid or missing.
    """
    if not admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication is not configured.",
        )

    if not token.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Use 'Bearer <token>'.",
        )

    provided = token.removeprefix("Bearer ")
    if provided != admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin token.",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_auth.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/auth.py tests/unit/test_auth.py
git commit -m "feat: add admin token authentication"
```

---

### Task 2: Dashboard Overview Route

**Files:**
- Create: `craft_dashboard/routes/__init__.py`
- Create: `craft_dashboard/routes/dashboard.py`
- Modify: `craft_dashboard/templates/dashboard/index.html`
- Test: `tests/unit/routes/__init__.py`
- Test: `tests/unit/routes/test_dashboard.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/routes/__init__.py`:
```python
```

Create `tests/unit/routes/test_dashboard.py`:
```python
"""Tests for the dashboard routes."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from craft_dashboard.app import create_app


class TestDashboardIndex:
    """Tests for the dashboard index route."""

    def test_index_returns_html(self) -> None:
        """GET / returns HTML with dashboard content."""
        app = create_app()
        client = TestClient(app)

        response = client.get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "craft-dashboard" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/routes/test_dashboard.py -v`
Expected: FAIL (or PASS if index route already works from Plan 1 Task 6 — either way, this establishes the test pattern)

- [ ] **Step 3: Create router module**

Create `craft_dashboard/routes/__init__.py`:
```python
"""FastAPI route modules."""
```

Create `craft_dashboard/routes/dashboard.py`:
```python
"""Dashboard overview routes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the main dashboard overview.

    Shows summary stats: total projects, open issues, open PRs,
    issues needing action, and per-project breakdown.
    """
    templates: Jinja2Templates = request.app.state.templates

    # Summary counts
    project_count = await session.scalar(select(func.count(Project.id)))
    open_issues = await session.scalar(
        select(func.count(Issue.id)).where(
            Issue.state == "open", Issue.issue_type == "issue"
        )
    )
    open_prs = await session.scalar(
        select(func.count(Issue.id)).where(
            Issue.state == "open", Issue.issue_type == "pull_request"
        )
    )

    # Per-project summary
    project_stats = await session.execute(
        select(
            Project.name,
            Project.category,
            func.count(Issue.id).filter(
                Issue.state == "open", Issue.issue_type == "issue"
            ).label("open_issues"),
            func.count(Issue.id).filter(
                Issue.state == "open", Issue.issue_type == "pull_request"
            ).label("open_prs"),
        )
        .outerjoin(Issue, Issue.project_id == Project.id)
        .group_by(Project.id)
        .order_by(Project.display_order)
    )

    projects = [
        {
            "name": row.name,
            "category": row.category,
            "open_issues": row.open_issues,
            "open_prs": row.open_prs,
        }
        for row in project_stats
    ]

    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "project_count": project_count or 0,
            "open_issues": open_issues or 0,
            "open_prs": open_prs or 0,
            "projects": projects,
        },
    )
```

- [ ] **Step 4: Update the dashboard template**

Replace `craft_dashboard/templates/dashboard/index.html`:
```html
{% extends "base.html" %}
{% block title %}Dashboard — craft-dashboard{% endblock %}
{% block content %}
<h1>Dashboard</h1>

<div class="grid">
  <article>
    <header>Projects</header>
    <p style="font-size: 2rem; font-weight: bold;">{{ project_count }}</p>
  </article>
  <article>
    <header>Open Issues</header>
    <p style="font-size: 2rem; font-weight: bold;">{{ open_issues }}</p>
  </article>
  <article>
    <header>Open PRs</header>
    <p style="font-size: 2rem; font-weight: bold;">{{ open_prs }}</p>
  </article>
</div>

<h2>Projects</h2>
<table>
  <thead>
    <tr>
      <th>Project</th>
      <th>Category</th>
      <th>Open Issues</th>
      <th>Open PRs</th>
    </tr>
  </thead>
  <tbody>
    {% for project in projects %}
    <tr>
      <td><a href="/issues?project={{ project.name }}">{{ project.name }}</a></td>
      <td>{{ project.category }}</td>
      <td>{{ project.open_issues }}</td>
      <td>{{ project.open_prs }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 5: Update `craft_dashboard/app.py` to use routers and store templates in app state**

Replace `craft_dashboard/app.py`:
```python
"""FastAPI application factory for craft-dashboard."""

import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.dependencies import set_session_factory
from craft_dashboard.routes.dashboard import router as dashboard_router
from craft_dashboard.settings import Settings

_PACKAGE_DIR = pathlib.Path(__file__).parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    settings = Settings()
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)
    set_session_factory(session_factory)
    app.state.settings = settings
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        A configured FastAPI instance.
    """
    app = FastAPI(
        title="craft-dashboard",
        description="Dashboard, insights, and issue triage for *craft applications.",
        lifespan=lifespan,
    )

    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok"}

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc) -> HTMLResponse:
        """Render a friendly 404 page."""
        templates: Jinja2Templates = request.app.state.templates
        return templates.TemplateResponse(
            request, "errors/404.html", status_code=404
        )

    @app.exception_handler(500)
    async def server_error_handler(request: Request, exc) -> HTMLResponse:
        """Render a friendly 500 page."""
        templates: Jinja2Templates = request.app.state.templates
        return templates.TemplateResponse(
            request, "errors/500.html", status_code=500
        )

    app.include_router(dashboard_router)

    return app
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/unit/routes/test_dashboard.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add craft_dashboard/routes/ craft_dashboard/app.py craft_dashboard/templates/dashboard/index.html tests/unit/routes/
git commit -m "feat: add dashboard overview route with project summary"
```

---

### Task 3: Issue List Route with HTMX Filtering

**Files:**
- Create: `craft_dashboard/routes/issues.py`
- Create: `craft_dashboard/templates/issues/list.html`
- Create: `craft_dashboard/templates/issues/partials/issue_table.html`
- Create: `craft_dashboard/templates/components/filters.html`
- Create: `craft_dashboard/templates/components/pagination.html`
- Create: `craft_dashboard/templates/components/score_badge.html`
- Test: `tests/unit/routes/test_issues.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/routes/test_issues.py`:
```python
"""Tests for the issue triage routes."""

from fastapi.testclient import TestClient

from craft_dashboard.app import create_app


class TestIssueList:
    """Tests for the issue list route."""

    def test_issues_page_returns_html(self) -> None:
        """GET /issues returns HTML."""
        app = create_app()
        client = TestClient(app)

        response = client.get("/issues")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_issues_page_has_filters(self) -> None:
        """Issues page includes filter controls."""
        app = create_app()
        client = TestClient(app)

        response = client.get("/issues")

        assert response.status_code == 200
        assert "hx-get" in response.text or "filter" in response.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/routes/test_issues.py -v`
Expected: FAIL

- [ ] **Step 3: Create the score badge component**

Create `craft_dashboard/templates/components/score_badge.html`:
```html
{# Score badge component. Usage: {% include "components/score_badge.html" with context %} #}
{# Expects: score_value (int 0-100), score_name (str) #}
{% if score_value is not none %}
<span class="score-badge {% if score_value >= 70 %}score-high{% elif score_value >= 40 %}score-medium{% else %}score-low{% endif %}"
      title="{{ score_name }}: {{ score_value }}/100">
  {{ score_value }}
</span>
{% endif %}
```

- [ ] **Step 4: Create the pagination component**

Create `craft_dashboard/templates/components/pagination.html`:
```html
{# Pagination component. Expects: page (int), total_pages (int), base_url (str) #}
{% if total_pages > 1 %}
<nav aria-label="Pagination">
  <ul>
    {% if page > 1 %}
    <li><a href="{{ base_url }}&page={{ page - 1 }}" hx-get="{{ base_url }}&page={{ page - 1 }}" hx-target="#issue-table" hx-swap="outerHTML">&laquo; Previous</a></li>
    {% endif %}
    <li><span>Page {{ page }} of {{ total_pages }}</span></li>
    {% if page < total_pages %}
    <li><a href="{{ base_url }}&page={{ page + 1 }}" hx-get="{{ base_url }}&page={{ page + 1 }}" hx-target="#issue-table" hx-swap="outerHTML">Next &raquo;</a></li>
    {% endif %}
  </ul>
</nav>
{% endif %}
```

- [ ] **Step 5: Create the issue table partial (for HTMX swapping)**

Create `craft_dashboard/templates/issues/partials/issue_table.html`:
```html
{# Partial: issue table body, swapped via HTMX #}
<div id="issue-table">
  <table>
    <thead>
      <tr>
        <th>Project</th>
        <th>Source</th>
        <th>Type</th>
        <th>Title</th>
        <th>Author</th>
        <th>Age</th>
        <th>Staleness</th>
        <th>Action</th>
        <th>Summary</th>
      </tr>
    </thead>
    <tbody>
      {% for issue in issues %}
      <tr>
        <td>{{ issue.project_name }}</td>
        <td>{{ issue.source }}</td>
        <td>{{ issue.issue_type }}</td>
        <td><a href="{{ issue.url }}" target="_blank" rel="noopener">{{ issue.title|truncate(80) }}</a></td>
        <td>{{ issue.author or "unknown" }}</td>
        <td>{{ issue.age_days }}d</td>
        <td>
          {% if issue.staleness is not none %}
          {% set score_value = issue.staleness %}
          {% set score_name = "Staleness" %}
          {% include "components/score_badge.html" %}
          {% else %}
          <span class="score-badge" style="background:#ccc;color:#666;">—</span>
          {% endif %}
        </td>
        <td>
          {% if issue.suggested_action %}
          <span class="action-badge">{{ issue.suggested_action|replace("_", " ") }}</span>
          {% endif %}
        </td>
        <td>{{ (issue.summary or "")|truncate(120) }}</td>
      </tr>
      {% else %}
      <tr>
        <td colspan="9">No issues found matching the current filters.</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  {% set base_url = "/issues/table?project=" ~ (filter_project or "") ~ "&source=" ~ (filter_source or "") ~ "&type=" ~ (filter_type or "") ~ "&action=" ~ (filter_action or "") ~ "&sort=" ~ (sort_by or "staleness") %}
  {% include "components/pagination.html" %}
</div>
```

- [ ] **Step 6: Create the issues list page**

Create `craft_dashboard/templates/issues/list.html`:
```html
{% extends "base.html" %}
{% block title %}Issues — craft-dashboard{% endblock %}
{% block content %}
<h1>Issue & PR Triage</h1>

<div class="filter-bar">
  <select name="project"
          hx-get="/issues/table"
          hx-target="#issue-table"
          hx-swap="outerHTML"
          hx-include=".filter-bar [name]">
    <option value="">All Projects</option>
    {% for p in project_names %}
    <option value="{{ p }}" {% if filter_project == p %}selected{% endif %}>{{ p }}</option>
    {% endfor %}
  </select>

  <select name="source"
          hx-get="/issues/table"
          hx-target="#issue-table"
          hx-swap="outerHTML"
          hx-include=".filter-bar [name]">
    <option value="">All Sources</option>
    <option value="github" {% if filter_source == "github" %}selected{% endif %}>GitHub</option>
    <option value="launchpad" {% if filter_source == "launchpad" %}selected{% endif %}>Launchpad</option>
  </select>

  <select name="type"
          hx-get="/issues/table"
          hx-target="#issue-table"
          hx-swap="outerHTML"
          hx-include=".filter-bar [name]">
    <option value="">All Types</option>
    <option value="issue" {% if filter_type == "issue" %}selected{% endif %}>Issues</option>
    <option value="pull_request" {% if filter_type == "pull_request" %}selected{% endif %}>PRs</option>
  </select>

  <select name="action"
          hx-get="/issues/table"
          hx-target="#issue-table"
          hx-swap="outerHTML"
          hx-include=".filter-bar [name]">
    <option value="">All Actions</option>
    <option value="close_stale">Close (Stale)</option>
    <option value="close_duplicate">Close (Duplicate)</option>
    <option value="close_not_a_bug">Close (Not a Bug)</option>
    <option value="close_outdated">Close (Outdated)</option>
    <option value="needs_triage">Needs Triage</option>
    <option value="needs_review">Needs Review</option>
    <option value="needs_rebase">Needs Rebase</option>
    <option value="keep_open">Keep Open</option>
  </select>

  <select name="sort"
          hx-get="/issues/table"
          hx-target="#issue-table"
          hx-swap="outerHTML"
          hx-include=".filter-bar [name]">
    <option value="staleness" {% if sort_by == "staleness" %}selected{% endif %}>Sort: Staleness ↓</option>
    <option value="age" {% if sort_by == "age" %}selected{% endif %}>Sort: Age ↓</option>
    <option value="updated" {% if sort_by == "updated" %}selected{% endif %}>Sort: Recently Updated</option>
  </select>
</div>

{% include "issues/partials/issue_table.html" %}
{% endblock %}
```

- [ ] **Step 7: Create the issues route module**

Create `craft_dashboard/routes/issues.py`:
```python
"""Issue and PR triage routes."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project

router = APIRouter(prefix="/issues")

ITEMS_PER_PAGE = 50


def _compute_age_days(created_at: datetime | None) -> int:
    """Compute days since creation."""
    if created_at is None:
        return 0
    now = datetime.now(tz=timezone.utc)
    created = created_at.replace(tzinfo=timezone.utc) if created_at.tzinfo is None else created_at
    return (now - created).days


async def _query_issues(
    session: AsyncSession,
    *,
    project: str = "",
    source: str = "",
    issue_type: str = "",
    action: str = "",
    sort_by: str = "staleness",
    page: int = 1,
) -> tuple[list[dict], int]:
    """Query issues with filters and return (issues, total_pages).

    Args:
        session: Database session.
        project: Filter by project name.
        source: Filter by source (github/launchpad).
        issue_type: Filter by type (issue/pull_request).
        action: Filter by suggested action.
        sort_by: Sort field.
        page: Page number (1-indexed).

    Returns:
        Tuple of (list of issue dicts, total page count).
    """
    query = (
        select(
            Issue,
            Project.name.label("project_name"),
            LLMEvaluation.summary,
            LLMEvaluation.suggested_action,
            LLMEvaluation.scores,
        )
        .join(Project, Issue.project_id == Project.id)
        .outerjoin(
            LLMEvaluation,
            (LLMEvaluation.issue_id == Issue.id) & LLMEvaluation.latest.is_(True),
        )
        .where(Issue.state == "open")
    )

    if project:
        query = query.where(Project.name == project)
    if source:
        query = query.where(Issue.source == source)
    if issue_type:
        query = query.where(Issue.issue_type == issue_type)
    if action:
        query = query.where(LLMEvaluation.suggested_action == action)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = await session.scalar(count_query) or 0
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)

    # Sort
    if sort_by == "age":
        query = query.order_by(Issue.created_at.asc())
    elif sort_by == "updated":
        query = query.order_by(Issue.updated_at.desc())
    else:
        # Default: staleness score descending (nulls last)
        query = query.order_by(
            func.coalesce(
                LLMEvaluation.scores["staleness"].as_float(), 0
            ).desc()
        )

    # Paginate
    offset = (page - 1) * ITEMS_PER_PAGE
    query = query.offset(offset).limit(ITEMS_PER_PAGE)

    result = await session.execute(query)

    issues = []
    for row in result:
        issue = row[0]
        scores = row.scores or {}
        issues.append({
            "project_name": row.project_name,
            "source": issue.source,
            "issue_type": issue.issue_type,
            "title": issue.title,
            "author": issue.author,
            "url": issue.url,
            "age_days": _compute_age_days(issue.created_at),
            "staleness": scores.get("staleness"),
            "suggested_action": row.suggested_action,
            "summary": row.summary,
        })

    return issues, total_pages


@router.get("", response_class=HTMLResponse)
async def issue_list(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    project: str = Query("", alias="project"),
    source: str = Query("", alias="source"),
    type: str = Query("", alias="type"),
    action: str = Query("", alias="action"),
    sort: str = Query("staleness", alias="sort"),
    page: int = Query(1, ge=1),
) -> HTMLResponse:
    """Render the issue triage list page."""
    templates: Jinja2Templates = request.app.state.templates

    issues, total_pages = await _query_issues(
        session,
        project=project,
        source=source,
        issue_type=type,
        action=action,
        sort_by=sort,
        page=page,
    )

    # Get project names for filter dropdown
    project_result = await session.execute(
        select(Project.name).order_by(Project.display_order)
    )
    project_names = [row.name for row in project_result]

    return templates.TemplateResponse(
        request,
        "issues/list.html",
        {
            "issues": issues,
            "project_names": project_names,
            "filter_project": project,
            "filter_source": source,
            "filter_type": type,
            "filter_action": action,
            "sort_by": sort,
            "page": page,
            "total_pages": total_pages,
        },
    )


@router.get("/table", response_class=HTMLResponse)
async def issue_table_partial(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    project: str = Query("", alias="project"),
    source: str = Query("", alias="source"),
    type: str = Query("", alias="type"),
    action: str = Query("", alias="action"),
    sort: str = Query("staleness", alias="sort"),
    page: int = Query(1, ge=1),
) -> HTMLResponse:
    """Return just the issue table partial (for HTMX swapping)."""
    templates: Jinja2Templates = request.app.state.templates

    issues, total_pages = await _query_issues(
        session,
        project=project,
        source=source,
        issue_type=type,
        action=action,
        sort_by=sort,
        page=page,
    )

    return templates.TemplateResponse(
        request,
        "issues/partials/issue_table.html",
        {
            "issues": issues,
            "filter_project": project,
            "filter_source": source,
            "filter_type": type,
            "filter_action": action,
            "sort_by": sort,
            "page": page,
            "total_pages": total_pages,
        },
    )
```

- [ ] **Step 8: Register the issues router in `app.py`**

Add to `craft_dashboard/app.py` after the dashboard router import:
```python
from craft_dashboard.routes.issues import router as issues_router
```

And in `create_app()`, after `app.include_router(dashboard_router)`:
```python
    app.include_router(issues_router)
```

- [ ] **Step 9: Run tests to verify**

Run: `uv run pytest tests/unit/routes/ -v`
Expected: All tests PASS

- [ ] **Step 10: Commit**

```bash
git add craft_dashboard/routes/issues.py craft_dashboard/templates/ craft_dashboard/app.py tests/unit/routes/test_issues.py
git commit -m "feat: add issue triage list with HTMX filtering and pagination"
```

---

### Task 4: Stats — Dependency Table Route

**Files:**
- Create: `craft_dashboard/routes/stats.py`
- Create: `craft_dashboard/templates/stats/dependencies.html`
- Test: `tests/unit/routes/test_stats.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/routes/test_stats.py`:
```python
"""Tests for the stats routes."""

from fastapi.testclient import TestClient

from craft_dashboard.app import create_app


class TestStatsRoutes:
    """Tests for stats routes."""

    def test_dependencies_page(self) -> None:
        """GET /stats/dependencies returns HTML."""
        app = create_app()
        client = TestClient(app)

        response = client.get("/stats/dependencies")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_releases_page(self) -> None:
        """GET /stats/releases returns HTML."""
        app = create_app()
        client = TestClient(app)

        response = client.get("/stats/releases")

        assert response.status_code == 200

    def test_trends_page(self) -> None:
        """GET /stats/trends returns HTML."""
        app = create_app()
        client = TestClient(app)

        response = client.get("/stats/trends")

        assert response.status_code == 200

    def test_stats_index_redirects(self) -> None:
        """GET /stats redirects to /stats/dependencies."""
        app = create_app()
        client = TestClient(app, follow_redirects=False)

        response = client.get("/stats")

        assert response.status_code in (301, 302, 307)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/routes/test_stats.py -v`
Expected: FAIL

- [ ] **Step 3: Create the dependencies template**

Create `craft_dashboard/templates/stats/dependencies.html`:
```html
{% extends "base.html" %}
{% block title %}Dependencies — craft-dashboard{% endblock %}
{% block content %}
<h1>Dependency Usage</h1>
<p>Which *craft libraries are used by which applications, and on which branches.</p>

<table>
  <thead>
    <tr>
      <th>Application</th>
      <th>Branch</th>
      <th>Dependency</th>
      <th>Version Spec</th>
    </tr>
  </thead>
  <tbody>
    {% for dep in dependencies %}
    <tr>
      <td>{{ dep.project_name }}</td>
      <td>{{ dep.branch }}</td>
      <td>{{ dep.dependency_name }}</td>
      <td><code>{{ dep.version_spec or "any" }}</code></td>
    </tr>
    {% else %}
    <tr>
      <td colspan="4">No dependency data available yet. Run data collection first.</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 4: Create the releases template**

Create `craft_dashboard/templates/stats/releases.html`:
```html
{% extends "base.html" %}
{% block title %}Releases — craft-dashboard{% endblock %}
{% block content %}
<h1>Releases</h1>

<table>
  <thead>
    <tr>
      <th>Project</th>
      <th>Version</th>
      <th>Branch</th>
      <th>Released</th>
    </tr>
  </thead>
  <tbody>
    {% for rel in releases %}
    <tr>
      <td>{{ rel.project_name }}</td>
      <td>{{ rel.version }}</td>
      <td>{{ rel.branch or "—" }}</td>
      <td>{{ rel.released_at.strftime("%Y-%m-%d") if rel.released_at else "—" }}</td>
    </tr>
    {% else %}
    <tr>
      <td colspan="4">No release data available yet.</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 5: Create the trends template**

Create `craft_dashboard/templates/stats/trends.html`:
```html
{% extends "base.html" %}
{% block title %}Trends — craft-dashboard{% endblock %}
{% block head %}
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
{% endblock %}
{% block content %}
<h1>Issue & PR Trends</h1>

<div class="filter-bar">
  <select id="trend-project"
          hx-get="/stats/trends/data"
          hx-target="#trend-chart-container"
          hx-swap="innerHTML">
    {% for p in project_names %}
    <option value="{{ p }}">{{ p }}</option>
    {% endfor %}
  </select>
</div>

<div id="trend-chart-container">
  <canvas id="trendChart" width="800" height="400"></canvas>
</div>
{% endblock %}

{% block scripts %}
<script>
document.addEventListener('DOMContentLoaded', function() {
    if (typeof chartData !== 'undefined') {
        const ctx = document.getElementById('trendChart').getContext('2d');
        new Chart(ctx, {
            type: 'line',
            data: chartData,
            options: {
                responsive: true,
                scales: {
                    x: { title: { display: true, text: 'Date' } },
                    y: { title: { display: true, text: 'Count' }, beginAtZero: true }
                }
            }
        });
    }
});
</script>
{% endblock %}
```

- [ ] **Step 6: Create the stats route module**

Create `craft_dashboard/routes/stats.py`:
```python
"""Stats routes for dependencies, releases, and trends."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.dependency import Dependency
from craft_dashboard.models.project import Project
from craft_dashboard.models.release import Release
from craft_dashboard.models.snapshot import Snapshot

router = APIRouter(prefix="/stats")


@router.get("", response_class=RedirectResponse)
async def stats_index() -> RedirectResponse:
    """Redirect /stats to /stats/dependencies."""
    return RedirectResponse(url="/stats/dependencies", status_code=302)


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
        }
        for row in result
    ]

    return templates.TemplateResponse(
        request,
        "stats/dependencies.html",
        {"dependencies": dependencies},
    )


@router.get("/releases", response_class=HTMLResponse)
async def releases_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the releases table."""
    templates: Jinja2Templates = request.app.state.templates

    result = await session.execute(
        select(
            Release.version,
            Release.branch,
            Release.released_at,
            Project.name.label("project_name"),
        )
        .join(Project, Release.project_id == Project.id)
        .order_by(Release.released_at.desc().nullslast())
    )

    releases = [
        {
            "project_name": row.project_name,
            "version": row.version,
            "branch": row.branch,
            "released_at": row.released_at,
        }
        for row in result
    ]

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


@router.get("/trends/data", response_class=JSONResponse)
async def trends_data(
    session: AsyncSession = Depends(get_db_session),
    project: str = Query(...),
) -> JSONResponse:
    """Return trend data as JSON for Chart.js.

    Args:
        session: Database session.
        project: Project name to get trends for.

    Returns:
        JSON with Chart.js-compatible dataset.
    """
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

    dates = []
    open_issues = []
    open_prs = []
    open_bugs = []

    for row in result:
        dates.append(row.snapshot_date.isoformat())
        open_issues.append(row.open_issues)
        open_prs.append(row.open_prs)
        open_bugs.append(row.open_bugs)

    return JSONResponse({
        "labels": dates,
        "datasets": [
            {"label": "Open Issues", "data": open_issues, "borderColor": "#4e79a7"},
            {"label": "Open PRs", "data": open_prs, "borderColor": "#f28e2b"},
            {"label": "Open Bugs", "data": open_bugs, "borderColor": "#e15759"},
        ],
    })
```

- [ ] **Step 7: Register the stats router in `app.py`**

Add to `craft_dashboard/app.py`:
```python
from craft_dashboard.routes.stats import router as stats_router
```

And in `create_app()`:
```python
    app.include_router(stats_router)
```

- [ ] **Step 8: Run tests to verify**

Run: `uv run pytest tests/unit/routes/test_stats.py -v`
Expected: All 4 tests PASS

- [ ] **Step 9: Commit**

```bash
git add craft_dashboard/routes/stats.py craft_dashboard/templates/stats/ craft_dashboard/app.py tests/unit/routes/test_stats.py
git commit -m "feat: add stats routes for dependencies, releases, and trends"
```

---

### Task 5: Admin Routes

**Files:**
- Create: `craft_dashboard/routes/admin.py`
- Test: `tests/unit/routes/test_admin.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/routes/test_admin.py`:
```python
"""Tests for admin routes."""

from fastapi.testclient import TestClient

from craft_dashboard.app import create_app


class TestAdminRoutes:
    """Tests for admin routes."""

    def test_admin_refresh_requires_auth(self) -> None:
        """POST /admin/refresh returns 401 without token."""
        app = create_app()
        client = TestClient(app)

        response = client.post("/admin/refresh")

        assert response.status_code == 401

    def test_admin_refresh_rejects_bad_token(self) -> None:
        """POST /admin/refresh returns 401 with wrong token."""
        app = create_app()
        client = TestClient(app)

        response = client.post(
            "/admin/refresh",
            headers={"Authorization": "Bearer wrong-token"},
        )

        assert response.status_code == 401

    def test_admin_health_requires_auth(self) -> None:
        """GET /admin/health returns 401 without token."""
        app = create_app()
        client = TestClient(app)

        response = client.get("/admin/health")

        assert response.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/routes/test_admin.py -v`
Expected: FAIL

- [ ] **Step 3: Write the admin routes**

Create `craft_dashboard/routes/admin.py`:
```python
"""Admin routes for triggering refreshes and re-evaluations."""

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.auth import verify_admin_token
from craft_dashboard.dependencies import get_db_session

router = APIRouter(prefix="/admin")


def _get_admin_token(request: Request) -> str:
    """Get the admin token from app settings."""
    return request.app.state.settings.admin_token


@router.post("/refresh")
async def trigger_refresh(
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Trigger a data refresh for all projects.

    Requires admin authentication via Bearer token.
    """
    admin_token = _get_admin_token(request)
    verify_admin_token(authorization, admin_token)

    # In a full implementation, this would trigger the collection pipeline
    return JSONResponse(
        {"status": "refresh_queued", "message": "Data refresh has been queued."},
        status_code=202,
    )


@router.post("/re-evaluate")
async def trigger_re_evaluation(
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Trigger LLM re-evaluation of all open issues.

    Requires admin authentication via Bearer token.
    """
    admin_token = _get_admin_token(request)
    verify_admin_token(authorization, admin_token)

    return JSONResponse(
        {"status": "evaluation_queued", "message": "LLM re-evaluation has been queued."},
        status_code=202,
    )


@router.get("/health")
async def admin_health(
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Detailed health check: DB connectivity and collection status.

    Requires admin authentication — exposes internal state that could aid
    targeted attacks (e.g., which projects are failing to collect).
    """
    from sqlalchemy import select, text

    from craft_dashboard.models.refresh_schedule import RefreshSchedule

    admin_token = _get_admin_token(request)
    verify_admin_token(authorization, admin_token)

    # Check DB connectivity
    try:
        await session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        db_status = f"error: {exc}"

    # Find projects with consecutive failures
    result = await session.execute(
        select(RefreshSchedule)
        .where(RefreshSchedule.consecutive_failures > 0)
        .order_by(RefreshSchedule.consecutive_failures.desc())
    )
    failing = [
        {
            "project_id": row.project_id,
            "source": row.source,
            "consecutive_failures": row.consecutive_failures,
            "last_error": row.last_error,
            "last_refreshed_at": row.last_refreshed_at.isoformat()
            if row.last_refreshed_at
            else None,
        }
        for row in result.scalars()
    ]

    return JSONResponse({
        "status": "ok" if db_status == "ok" else "degraded",
        "database": db_status,
        "failing_collectors": failing,
    })
```

- [ ] **Step 4: Register the admin router in `app.py`**

Add to `craft_dashboard/app.py`:
```python
from craft_dashboard.routes.admin import router as admin_router
```

And in `create_app()`:
```python
    app.include_router(admin_router)
```

- [ ] **Step 5: Run tests to verify**

Run: `uv run pytest tests/unit/routes/test_admin.py -v`
Expected: All 2 tests PASS

- [ ] **Step 6: Commit**

```bash
git add craft_dashboard/routes/admin.py craft_dashboard/app.py tests/unit/routes/test_admin.py
git commit -m "feat: add admin routes with token authentication"
```

---

### Task 6: Navigation Update — Link All Pages

**Files:**
- Modify: `craft_dashboard/templates/base.html`

- [ ] **Step 1: Update the navigation bar in `base.html`**

Update the `<nav>` section in `craft_dashboard/templates/base.html`:
```html
    <nav class="container">
      <ul>
        <li><strong>craft-dashboard</strong></li>
      </ul>
      <ul>
        <li><a href="/">Dashboard</a></li>
        <li><a href="/issues">Issues & PRs</a></li>
        <li>
          <details class="dropdown">
            <summary>Stats</summary>
            <ul>
              <li><a href="/stats/dependencies">Dependencies</a></li>
              <li><a href="/stats/releases">Releases</a></li>
              <li><a href="/stats/trends">Trends</a></li>
            </ul>
          </details>
        </li>
      </ul>
    </nav>
```

- [ ] **Step 2: Run all tests to verify nothing broke**

Run: `uv run pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add craft_dashboard/templates/base.html
git commit -m "feat: update navigation with links to all pages"
```

---

### Task 7: Run Full Test Suite and Lint

**Files:**
- No new files

- [ ] **Step 1: Run the full test suite**

Run: `make test`
Expected: All tests PASS

- [ ] **Step 2: Format and lint**

Run: `make format && make lint`
Expected: No errors

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "chore: lint and format pass for web dashboard"
```
