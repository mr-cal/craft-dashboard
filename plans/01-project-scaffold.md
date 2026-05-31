# Plan 1: Project Scaffold & Configuration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up the craft-dashboard project with FastAPI skeleton, configuration system, and development tooling.

**Architecture:** A Python package (`craft_dashboard`) with FastAPI as the web framework, Pydantic for config validation, and uv for dependency management. The project uses the same code quality conventions as starcraft-stats (ruff, pytest, etc.) but simplified where appropriate.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, uv, ruff, pytest

> **Existing code to read before implementing:** `starcraft_stats/config.py` (config structure and conventions), `pyproject.toml` (existing deps and tool config), `.pre-commit-config.yaml` (linting setup to carry forward).

---

### Task 1: Initialize Project Structure

**Files:**
- Create: `pyproject.toml`
- Create: `craft_dashboard/__init__.py`
- Create: `Makefile`
- Create: `README.md`
- Create: `.gitignore`
- Create: `.envrc`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "craft-dashboard"
description = "Dashboard, insights, and issue triage for *craft applications and libraries."
dynamic = ["version"]
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "gunicorn>=23.0",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pydantic>=2.8",
    "pydantic-settings>=2.6",
    "jinja2>=3.1",
    "httpx>=0.28",
    "tenacity>=0.29",
    "pygithub>=2.3",
    "launchpadlib>=2.0",
    "click>=8.1",
    "python-multipart>=0.0.18",
]
classifiers = [
    "Development Status :: 2 - Pre-Alpha",
    "License :: OSI Approved :: GNU General Public License (GPL)",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.12",
]
requires-python = ">=3.12"
readme = { file = "README.md", content-type = "text/markdown" }

[project.scripts]
craft-dashboard = "craft_dashboard.cli:main"

[dependency-groups]
lint = [
    "ruff>=0.9",
]
types = [
    "ty>=0.0.1a6",
]
dev = [
    "coverage[toml]~=7.4",
    "pytest~=9.0",
    "pytest-asyncio~=0.25",
    "pytest-cov~=7.0",
    "pytest-mock~=3.12",
    "httpx",
]

[build-system]
requires = [
    "setuptools>=69.0",
    "setuptools_scm[toml]>=7.1",
]
build-backend = "setuptools.build_meta"

[tool.setuptools_scm]
write_to = "craft_dashboard/_version.py"
version_scheme = "post-release"
git_describe_command = [
    "git",
    "describe",
    "--dirty",
    "--long",
    "--match",
    "[0-9]*.[0-9]*.[0-9]*",
    "--exclude",
    "*[^0-9.]*",
]

[tool.setuptools.packages.find]
include = ["craft_dashboard*"]
namespaces = false

[tool.pytest.ini_options]
minversion = "8.0"
testpaths = "tests"
xfail_strict = true
asyncio_mode = "auto"

[tool.coverage.run]
branch = true
omit = ["tests/**"]

[tool.coverage.report]
skip_empty = true
exclude_also = [
    "if (typing\\.)?TYPE_CHECKING:",
]

[tool.ruff]
line-length = 88
target-version = "py312"
src = ["craft_dashboard", "tests"]

[tool.ruff.lint]
select = [
    "F", "E", "W", "I", "N", "D", "UP", "YTT",
    "ANN", "ASYNC",
    "S101", "S102", "S103", "S108", "S104", "S105", "S106", "S107",
    "S110", "S113", "S3", "S5", "S602", "S701",
    "BLE", "FBT", "B0", "A", "COM", "C4", "T10",
    "ISC", "ICN", "INP",
    "PIE790", "PIE794", "PIE796", "PIE804", "PIE807", "PIE810",
    "PYI", "PT", "Q", "RSE", "RET", "SLF", "SIM", "TID",
    "TC001", "TC002", "TC003", "TC004", "TC005",
    "ARG", "PTH", "FIX", "ERA", "PGH", "PL", "TRY",
    "FLY", "PERF",
    "RUF001", "RUF002", "RUF003", "RUF005", "RUF008",
    "B035", "RUF013", "RUF100", "RUF200",
]
ignore = [
    "E501", "D105", "D107", "D203", "D213", "D215",
    "A003", "SIM117", "PLW1641", "COM812", "ISC001", "TRY003",
]

[tool.ruff.lint.flake8-annotations]
allow-star-arg-any = true

[tool.ruff.lint.pydocstyle]
ignore-decorators = [
    "typing.overload",
    "overrides.override",
    "overrides.overrides",
    "typing.override",
    "typing_extensions.override",
]

[tool.ruff.lint.pylint]
max-args = 8

[tool.ruff.lint.per-file-ignores]
"tests/**.py" = [
    "D", "ANN", "ARG", "S101", "S103", "S108",
    "PLR0913", "PLR2004", "SLF",
]
"__init__.py" = ["I001", "F401"]
```

- [ ] **Step 2: Create `craft_dashboard/__init__.py`**

```python
"""Dashboard, insights, and issue triage for *craft applications and libraries."""
```

- [ ] **Step 3: Create `.gitignore`**

```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
*.egg
dist/
build/
.eggs/

# Virtual environments
.venv/
venv/

# IDE
.idea/
.vscode/
*.swp
*.swo

# Tools
.mypy_cache/
.ruff_cache/
.pytest_cache/
.coverage
htmlcov/

# Environment
.env
.envrc

# OS
.DS_Store

# uv
uv.lock
```

- [ ] **Step 4: Create `.envrc`**

```bash
layout python3
```

- [ ] **Step 5: Create `README.md`**

```markdown
# craft-dashboard

Dashboard, insights, and issue triage for the \*craft applications and libraries.

## Overview

craft-dashboard provides:
- **Issue & PR triage dashboard** with LLM-powered scoring and action suggestions
- **Statistics & trends** for open issues, PRs, releases, and dependencies
- **Multi-source data** from GitHub and Launchpad

## Development

### Prerequisites

- Python 3.12+
- PostgreSQL 16+
- uv

### Setup

```bash
# Install dependencies
uv sync --group dev

# Set up environment variables
cp .env.example .env
# Edit .env with your database URL and API tokens

# Run database migrations
uv run alembic upgrade head

# Start development server
uv run uvicorn craft_dashboard.app:create_app --factory --reload
```

### Testing

```bash
make test
```

### Linting

```bash
make lint
```
```

- [ ] **Step 6: Create `Makefile`**

```makefile
PROJECT := craft_dashboard
SOURCES := $(PROJECT) tests scripts

.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.PHONY: setup
setup:  ## Install all dependencies
	uv sync --group dev --group lint --group types

.PHONY: format
format:  ## Auto-format code with ruff
	uv run ruff check --fix $(SOURCES)
	uv run ruff format $(SOURCES)

.PHONY: lint
lint:  ## Lint with ruff and check types with ty
	uv run ruff check $(SOURCES)
	uv run ruff format --diff $(SOURCES)
	uv run ty check $(SOURCES)

.PHONY: test
test:  ## Run all tests
	uv run pytest

.PHONY: test-cov
test-cov:  ## Run tests with coverage report
	uv run pytest --cov=$(PROJECT) --cov-report=html --cov-report=term-missing

.PHONY: dev
dev:  ## Run development server with hot reload
	uv run uvicorn craft_dashboard.app:create_app --factory --reload --host 0.0.0.0 --port 8000

.PHONY: migrate
migrate:  ## Apply database migrations
	uv run alembic upgrade head

.PHONY: collect
collect:  ## Run data collection (all sources)
	uv run scripts/collect_data.py --source all

.PHONY: llm
llm:  ## Run LLM evaluation (open issues only)
	uv run scripts/run_llm.py --open-only

.PHONY: migrate-csv
migrate-csv:  ## One-time CSV migration from starcraft-stats
	uv run scripts/migrate_csv.py

.PHONY: clean
clean:  ## Clean build artifacts and caches
	rm -rf dist build .coverage htmlcov .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
	find . -name "*.pyc" -delete
```

- [ ] **Step 7: Commit**

```bash
git rm common.mk Makefile  # remove old starcraft-stats Makefile and common.mk
git add pyproject.toml craft_dashboard/__init__.py .gitignore .envrc README.md Makefile
git commit -m "feat: initialize craft-dashboard project structure"
```

---

### Task 2: Configuration System

**Files:**
- Create: `craft-dashboard.toml`
- Create: `craft_dashboard/config.py`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/__init__.py`:
```python
```

Create `tests/unit/__init__.py`:
```python
```

Create `tests/conftest.py`:
```python
"""Shared test fixtures for craft-dashboard."""

import pathlib

import pytest


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    """Path to test fixtures directory."""
    return pathlib.Path(__file__).parent / "fixtures"
```

Create `tests/unit/test_config.py`:
```python
"""Tests for the configuration system."""

import pathlib
import textwrap

import pytest

from craft_dashboard.config import DashboardConfig, load_config


class TestDashboardConfig:
    """Tests for DashboardConfig."""

    def test_load_config_from_file(self, tmp_path: pathlib.Path) -> None:
        """Load a valid config file."""
        config_file = tmp_path / "craft-dashboard.toml"
        config_file.write_text(
            textwrap.dedent("""\
                craft-applications = ["snapcraft", "charmcraft"]
                craft-libraries = ["craft-cli"]
                craft-projects = ["snapcraft", "charmcraft", "craft-cli"]
                refresh-interval-days = 7
                launchpad-projects = ["snapcraft"]
                maintainers = ["mr-cal"]

                [hotfix-min-versions]
                snapcraft = "8.0"
            """)
        )

        config = load_config(config_file)

        assert config.craft_applications == ["snapcraft", "charmcraft"]
        assert config.craft_libraries == ["craft-cli"]
        assert config.craft_projects == ["snapcraft", "charmcraft", "craft-cli"]
        assert config.refresh_interval_days == 7
        assert config.launchpad_projects == ["snapcraft"]
        assert config.maintainers == ["mr-cal"]
        assert config.hotfix_min_versions == {"snapcraft": "8.0"}

    def test_load_config_default_refresh_interval(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Default refresh interval is 7 days."""
        config_file = tmp_path / "craft-dashboard.toml"
        config_file.write_text(
            textwrap.dedent("""\
                craft-applications = []
                craft-libraries = []
                craft-projects = []
                launchpad-projects = []
                maintainers = []
            """)
        )

        config = load_config(config_file)

        assert config.refresh_interval_days == 7

    def test_load_config_missing_file(self, tmp_path: pathlib.Path) -> None:
        """Raise FileNotFoundError for missing config file."""
        config_file = tmp_path / "nonexistent.toml"

        with pytest.raises(FileNotFoundError):
            load_config(config_file)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'craft_dashboard.config'`

- [ ] **Step 3: Create the config file `craft-dashboard.toml`**

```toml
craft-applications = [
    "charmcraft",
    "imagecraft",
    "rockcraft",
    "snapcraft",
    "debcraft",
]

craft-libraries = [
    "craft-application",
    "craft-archives",
    "craft-cli",
    "craft-grammar",
    "craft-parts",
    "craft-platforms",
    "craft-providers",
    "craft-store",
]

craft-projects = [
    "charmcraft",
    "imagecraft",
    "rockcraft",
    "snapcraft",
    "debcraft",
    "craft-application",
    "craft-archives",
    "craft-cli",
    "craft-grammar",
    "craft-parts",
    "craft-platforms",
    "craft-providers",
    "craft-store",
    "craft-actions",
    "craft-artifacts",
    "snapcraft-rocks",
    "starbase",
    "starflow",
]

refresh-interval-days = 7

launchpad-projects = ["snapcraft"]

maintainers = [
    "alderic-coroir",
    "bepri",
    "cmatsuoka",
    "Copilot",
    "come-maiz",
    "cjdcordeiro",
    "cjp256",
    "dariuszd21",
    "dboddie",
    "dependabot[bot]",
    "facundobatista",
    "jahn-junior",
    "kyrofa",
    "lengau",
    "mattculler",
    "medubelko",
    "mr-cal",
    "renovate[bot]",
    "sergiusens",
    "smethnani",
    "steinbro",
    "syu-w",
    "tigarmo",
    "upils",
]

[hotfix-min-versions]
charmcraft = "3.0"
snapcraft = "8.0"
rockcraft = "1.15"
```

- [ ] **Step 4: Write minimal implementation of `craft_dashboard/config.py`**

```python
"""Configuration loading for craft-dashboard."""

import pathlib
import tomllib

from pydantic import BaseModel, Field


class DashboardConfig(BaseModel):
    """Configuration for the craft-dashboard application."""

    craft_applications: list[str] = Field(default_factory=list)
    craft_libraries: list[str] = Field(default_factory=list)
    craft_projects: list[str] = Field(default_factory=list)
    refresh_interval_days: int = 7
    launchpad_projects: list[str] = Field(default_factory=list)
    maintainers: list[str] = Field(default_factory=list)
    hotfix_min_versions: dict[str, str] = Field(default_factory=dict)


def load_config(config_path: pathlib.Path) -> DashboardConfig:
    """Load configuration from a TOML file.

    Args:
        config_path: Path to the TOML configuration file.

    Returns:
        A validated DashboardConfig instance.

    Raises:
        FileNotFoundError: If the config file does not exist.
    """
    if not config_path.exists():
        msg = f"Config file not found: {config_path}"
        raise FileNotFoundError(msg)

    with config_path.open("rb") as f:
        raw = tomllib.load(f)

    # Convert TOML kebab-case keys to Python snake_case
    normalized = {key.replace("-", "_"): value for key, value in raw.items()}

    # Handle nested sections
    if "hotfix_min_versions" in normalized:
        normalized["hotfix_min_versions"] = dict(normalized["hotfix_min_versions"])

    return DashboardConfig(**normalized)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_config.py -v`
Expected: All 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add craft-dashboard.toml craft_dashboard/config.py tests/
git commit -m "feat: add configuration system with TOML loading"
```

---

### Task 3: Environment Settings

**Files:**
- Create: `craft_dashboard/settings.py`
- Create: `.env.example`
- Test: `tests/unit/test_settings.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_settings.py`:
```python
"""Tests for application settings."""

from craft_dashboard.settings import Settings


class TestSettings:
    """Tests for Settings."""

    def test_default_settings(self, monkeypatch) -> None:
        """Settings load with defaults when env vars are not set."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")

        settings = Settings()

        assert settings.database_url == "postgresql+asyncpg://localhost/test"
        assert settings.debug is False
        assert settings.host == "0.0.0.0"
        assert settings.port == 8000

    def test_settings_from_env(self, monkeypatch) -> None:
        """Settings load from environment variables."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://db:5432/dashboard")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test123")
        monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")
        monkeypatch.setenv("DEBUG", "true")

        settings = Settings()

        assert settings.database_url == "postgresql+asyncpg://db:5432/dashboard"
        assert settings.github_token == "ghp_test123"
        assert settings.openrouter_api_key == "sk-or-test123"
        assert settings.admin_token == "admin-secret"
        assert settings.debug is True

    def test_default_llm_backend(self, monkeypatch) -> None:
        """Default LLM backend is openrouter."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")

        settings = Settings()

        assert settings.llm_backend == "openrouter"
        assert settings.local_llm_url == "http://localhost:11434/v1"

    def test_local_llm_backend(self, monkeypatch) -> None:
        """Local LLM backend can be configured via env vars."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        monkeypatch.setenv("LLM_BACKEND", "local")
        monkeypatch.setenv("LOCAL_LLM_URL", "http://192.168.1.10:11434/v1")
        monkeypatch.setenv("LOCAL_LLM_SUMMARY_MODEL", "qwen2.5")

        settings = Settings()

        assert settings.llm_backend == "local"
        assert settings.local_llm_url == "http://192.168.1.10:11434/v1"
        assert settings.local_llm_summary_model == "qwen2.5"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/settings.py`:
```python
"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings, loaded from environment variables or .env file."""

    database_url: str = "postgresql+asyncpg://localhost/craft_dashboard"
    github_token: str = ""
    openrouter_api_key: str = ""
    admin_token: str = ""
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    config_file: str = "craft-dashboard.toml"

    # LLM backend: "openrouter" (production) or "local" (local LLM server)
    llm_backend: str = "openrouter"

    # Local LLM settings (any OpenAI-compatible server)
    local_llm_url: str = "http://localhost:11434/v1"
    local_llm_summary_model: str = "llama3.2"
    local_llm_evaluation_model: str = "llama3.2"

    # OpenRouter model settings
    openrouter_summary_model: str = "google/gemini-flash-1.5"
    openrouter_evaluation_model: str = "anthropic/claude-sonnet-4-20250514"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
```

Create `.env.example`:
```bash
# Database
DATABASE_URL=postgresql+asyncpg://localhost/craft_dashboard

# GitHub API token (fine-grained, read-only public repos)
GITHUB_TOKEN=

# LLM backend: "openrouter" (production) or "local" (local LLM server)
LLM_BACKEND=openrouter

# OpenRouter API key (required when LLM_BACKEND=openrouter)
OPENROUTER_API_KEY=

# Local LLM settings (used when LLM_BACKEND=local)
# Point to any OpenAI-compatible server, e.g. http://192.168.1.10:8080/v1
LOCAL_LLM_URL=http://localhost:11434/v1
LOCAL_LLM_SUMMARY_MODEL=llama3.2
LOCAL_LLM_EVALUATION_MODEL=llama3.2

# Admin bearer token for protected endpoints
ADMIN_TOKEN=

# Debug mode (set to true for development)
DEBUG=false
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_settings.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/settings.py .env.example tests/unit/test_settings.py
git commit -m "feat: add environment settings with pydantic-settings"
```

---

### Task 4: FastAPI App Factory

**Files:**
- Create: `craft_dashboard/app.py`
- Test: `tests/unit/test_app.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_app.py`:
```python
"""Tests for the FastAPI application factory."""

from fastapi.testclient import TestClient

from craft_dashboard.app import create_app


class TestCreateApp:
    """Tests for create_app."""

    def test_app_returns_fastapi_instance(self) -> None:
        """create_app returns a FastAPI application."""
        app = create_app()
        assert app.title == "craft-dashboard"

    def test_health_endpoint(self) -> None:
        """The /health endpoint returns 200."""
        app = create_app()
        client = TestClient(app)

        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/app.py`:
```python
"""FastAPI application factory for craft-dashboard."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        A configured FastAPI instance.
    """
    app = FastAPI(
        title="craft-dashboard",
        description="Dashboard, insights, and issue triage for *craft applications.",
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok"}

    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_app.py -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/app.py tests/unit/test_app.py
git commit -m "feat: add FastAPI application factory with health endpoint"
```

---

### Task 5: CLI Entry Point

**Files:**
- Create: `craft_dashboard/cli.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli.py`:
```python
"""Tests for the CLI entry point."""

from click.testing import CliRunner

from craft_dashboard.cli import main


class TestCLI:
    """Tests for the CLI."""

    def test_main_help(self) -> None:
        """The --help flag shows usage information."""
        runner = CliRunner()

        result = runner.invoke(main, ["--help"])

        assert result.exit_code == 0
        assert "craft-dashboard" in result.output

    def test_serve_command_exists(self) -> None:
        """The 'serve' subcommand exists."""
        runner = CliRunner()

        result = runner.invoke(main, ["serve", "--help"])

        assert result.exit_code == 0
        assert "--host" in result.output
        assert "--port" in result.output

    def test_collect_command_exists(self) -> None:
        """The 'collect' subcommand exists."""
        runner = CliRunner()

        result = runner.invoke(main, ["collect", "--help"])

        assert result.exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/cli.py`:
```python
"""CLI entry point for craft-dashboard."""

import click
import uvicorn


@click.group()
def main() -> None:
    """craft-dashboard: Dashboard and insights for *craft applications."""


@main.command()
@click.option("--host", default="0.0.0.0", help="Bind host.")
@click.option("--port", default=8000, type=int, help="Bind port.")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development.")
def serve(*, host: str, port: int, reload: bool) -> None:
    """Start the web server."""
    uvicorn.run(
        "craft_dashboard.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )


@main.command()
@click.option(
    "--source",
    type=click.Choice(["github", "launchpad", "all"]),
    default="all",
    help="Data source to collect from.",
)
def collect(*, source: str) -> None:
    """Collect data from external sources."""
    click.echo(f"Collecting data from: {source}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cli.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/cli.py tests/unit/test_cli.py
git commit -m "feat: add CLI entry point with serve and collect commands"
```

---

### Task 6: Jinja2 Template Setup

**Files:**
- Create: `craft_dashboard/templates/base.html`
- Create: `craft_dashboard/templates/errors/404.html`
- Create: `craft_dashboard/templates/errors/500.html`
- Create: `craft_dashboard/static/css/custom.css`
- Modify: `craft_dashboard/app.py` (add template + static file mounting)
- Test: `tests/unit/test_app.py` (add template rendering test)

- [ ] **Step 1: Create `craft_dashboard/templates/base.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{% block title %}craft-dashboard{% endblock %}</title>
    <link
      rel="stylesheet"
      href="https://assets.ubuntu.com/v1/vanilla_framework_version_4_44_0_min.css"
    />
    <link rel="stylesheet" href="/static/css/custom.css" />
    <script
      src="https://unpkg.com/htmx.org@2.0.4"
      integrity="sha384-HGfztofotfshcF7+8n44JQL2oJmowVChPTg48S+jvZoztPfvwD79OC/LTtG6dMp+"
      crossorigin="anonymous"
    ></script>
    {% block head %}{% endblock %}
  </head>
  <body class="is-paper">
    <header id="navigation" class="p-navigation is-dark">
      <div class="p-navigation__row--25-75">
        <div class="p-navigation__banner">
          <div class="p-navigation__tagged-logo">
            <a class="p-navigation__link" href="/">
              <span class="p-navigation__logo-title">craft-dashboard</span>
            </a>
          </div>
        </div>
        <nav class="p-navigation__nav" aria-label="Main navigation">
          <ul class="p-navigation__items">
            <li class="p-navigation__item">
              <a class="p-navigation__link" href="/">Dashboard</a>
            </li>
            <li class="p-navigation__item">
              <a class="p-navigation__link" href="/issues">Issues</a>
            </li>
            <li class="p-navigation__item">
              <a class="p-navigation__link" href="/stats">Stats</a>
            </li>
          </ul>
        </nav>
      </div>
    </header>
    <main>
      {% block content %}{% endblock %}
    </main>
    <footer class="l-footer--sticky p-strip is-dark is-shallow">
      <div class="row">
        <a class="is-dark" href="https://github.com/mr-cal/craft-dashboard">craft-dashboard</a>
      </div>
    </footer>
    {% block scripts %}{% endblock %}
  </body>
</html>
```

- [ ] **Step 2: Create `craft_dashboard/static/css/custom.css`**

```css
/* Override grid max-width */
@supports (display: grid) {
  .p-navigation__row--25-75,
  .row--25-75,
  .row--50-50,
  .row {
    max-width: 120rem;
  }
}

.score-badge {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  font-size: 0.85rem;
  font-weight: 600;
}

.score-high {
  background-color: #dc3545;
  color: white;
}

.score-medium {
  background-color: #ffc107;
  color: black;
}

.score-low {
  background-color: #28a745;
  color: white;
}

.action-badge {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  font-size: 0.8rem;
  background-color: #6c757d;
  color: white;
}

.filter-bar {
  margin-bottom: 1rem;
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  align-items: center;
}
```

- [ ] **Step 3: Create placeholder index template**

Create `craft_dashboard/templates/dashboard/index.html`:
```html
{% extends "base.html" %}
{% block title %}Dashboard — craft-dashboard{% endblock %}
{% block content %}
<h1>Dashboard</h1>
<p>Welcome to craft-dashboard. Data views will be added in subsequent plans.</p>
{% endblock %}
```

- [ ] **Step 3b: Create error page templates**

Create `craft_dashboard/templates/errors/404.html`:
```html
{% extends "base.html" %}
{% block title %}Page Not Found — craft-dashboard{% endblock %}
{% block content %}
<hgroup>
  <h1>404</h1>
  <p>Page not found</p>
</hgroup>
<p>The page you're looking for doesn't exist. <a href="/">Go back to the dashboard</a>.</p>
{% endblock %}
```

Create `craft_dashboard/templates/errors/500.html`:
```html
{% extends "base.html" %}
{% block title %}Server Error — craft-dashboard{% endblock %}
{% block content %}
<hgroup>
  <h1>500</h1>
  <p>Internal server error</p>
</hgroup>
<p>Something went wrong on our end. Check the server logs for details. <a href="/">Go back to the dashboard</a>.</p>
{% endblock %}
```

- [ ] **Step 4: Update `craft_dashboard/app.py` to serve templates and static files**

Replace `craft_dashboard/app.py` with:
```python
"""FastAPI application factory for craft-dashboard."""

import pathlib

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

_PACKAGE_DIR = pathlib.Path(__file__).parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        A configured FastAPI instance.
    """
    app = FastAPI(
        title="craft-dashboard",
        description="Dashboard, insights, and issue triage for *craft applications.",
    )

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        """Render the main dashboard page."""
        return templates.TemplateResponse(request, "dashboard/index.html")

    return app
```

- [ ] **Step 5: Add a test for the index page**

Add to `tests/unit/test_app.py`:
```python
    def test_index_page(self) -> None:
        """The index page returns HTML."""
        app = create_app()
        client = TestClient(app)

        response = client.get("/")

        assert response.status_code == 200
        assert "craft-dashboard" in response.text
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_app.py -v`
Expected: All 3 tests PASS (health, app instance, index page)

- [ ] **Step 7: Commit**

```bash
git add craft_dashboard/app.py craft_dashboard/templates/ craft_dashboard/static/ tests/unit/test_app.py
git commit -m "feat: add Jinja2 templates, static files, and index page"
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

- [ ] **Step 3: Commit any formatting fixes**

```bash
git add -A
git commit -m "chore: fix any formatting issues from ruff"
```
