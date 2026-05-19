# Plan 2: Database & Models

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up PostgreSQL with SQLAlchemy async models, Alembic migrations, and database session management for craft-dashboard.

**Architecture:** SQLAlchemy 2.x with async support (asyncpg driver). Alembic manages schema migrations. All models use the declarative base pattern. The database module provides an async session factory that FastAPI routes use via dependency injection.

**Tech Stack:** SQLAlchemy 2.x (async), asyncpg, Alembic, PostgreSQL 16

> **Existing code to read before implementing:** `starcraft_stats/models/` (all files — field names and types used in practice), `starcraft_stats/models/github.py` and `issues.py` (which GitHub fields are actually consumed).

**Depends on:** Plan 1 (Project Scaffold)

---

### Task 1: Database Connection Module

**Files:**
- Create: `craft_dashboard/database.py`
- Test: `tests/unit/test_database.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_database.py`:
```python
"""Tests for database connection management."""

from unittest.mock import AsyncMock, patch

import pytest

from craft_dashboard.database import get_engine, get_session_factory


class TestGetEngine:
    """Tests for get_engine."""

    def test_creates_async_engine(self) -> None:
        """get_engine returns an AsyncEngine with the given URL."""
        engine = get_engine("postgresql+asyncpg://localhost/test_db")

        assert str(engine.url) == "postgresql+asyncpg://localhost/test_db"

    def test_engine_uses_pool_settings(self) -> None:
        """Engine has reasonable pool settings."""
        engine = get_engine("postgresql+asyncpg://localhost/test_db")

        assert engine.pool.size() == 5


class TestGetSessionFactory:
    """Tests for get_session_factory."""

    def test_creates_session_factory(self) -> None:
        """get_session_factory returns an async session maker."""
        engine = get_engine("postgresql+asyncpg://localhost/test_db")
        session_factory = get_session_factory(engine)

        assert session_factory is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_database.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'craft_dashboard.database'`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/database.py`:
```python
"""Database connection and session management."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def get_engine(database_url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine.

    Args:
        database_url: PostgreSQL connection URL with asyncpg driver.

    Returns:
        An AsyncEngine instance.
    """
    return create_async_engine(
        database_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
    )


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given engine.

    Args:
        engine: The async SQLAlchemy engine.

    Returns:
        An async_sessionmaker that produces AsyncSession instances.
    """
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_database.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/database.py tests/unit/test_database.py
git commit -m "feat: add async database engine and session factory"
```

---

### Task 2: SQLAlchemy Base and Mixins

**Files:**
- Create: `craft_dashboard/models/__init__.py`
- Create: `craft_dashboard/models/base.py`
- Test: `tests/unit/models/__init__.py`
- Test: `tests/unit/models/test_base.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/models/__init__.py`:
```python
```

Create `tests/unit/models/test_base.py`:
```python
"""Tests for the SQLAlchemy base model."""

from craft_dashboard.models.base import Base, TimestampMixin


class TestBase:
    """Tests for the declarative base."""

    def test_base_has_metadata(self) -> None:
        """Base has a metadata attribute."""
        assert Base.metadata is not None

    def test_timestamp_mixin_has_created_at(self) -> None:
        """TimestampMixin defines created_at column."""
        assert "created_at" in TimestampMixin.__dict__

    def test_timestamp_mixin_has_updated_at(self) -> None:
        """TimestampMixin defines updated_at column."""
        assert "updated_at" in TimestampMixin.__dict__
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/models/test_base.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/models/__init__.py`:
```python
"""SQLAlchemy models for craft-dashboard."""

from craft_dashboard.models.base import Base

__all__ = ["Base"]
```

Create `craft_dashboard/models/base.py`:
```python
"""SQLAlchemy declarative base and common mixins."""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""


class TimestampMixin:
    """Mixin that adds created_at and updated_at timestamp columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/models/test_base.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/models/ tests/unit/models/
git commit -m "feat: add SQLAlchemy declarative base with timestamp mixin"
```

---

### Task 3: Project Model

**Files:**
- Create: `craft_dashboard/models/project.py`
- Test: `tests/unit/models/test_project.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/models/test_project.py`:
```python
"""Tests for the Project model."""

from craft_dashboard.models.project import Project


class TestProjectModel:
    """Tests for the Project model."""

    def test_tablename(self) -> None:
        """Project model uses 'projects' table."""
        assert Project.__tablename__ == "projects"

    def test_required_columns(self) -> None:
        """Project model has all required columns."""
        column_names = {col.name for col in Project.__table__.columns}
        expected = {
            "id",
            "name",
            "category",
            "github_org",
            "launchpad_name",
            "display_order",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(column_names)

    def test_name_is_unique(self) -> None:
        """The name column has a unique constraint."""
        name_col = Project.__table__.columns["name"]
        assert name_col.unique is True

    def test_category_is_not_nullable(self) -> None:
        """The category column is not nullable."""
        category_col = Project.__table__.columns["category"]
        assert category_col.nullable is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/models/test_project.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/models/project.py`:
```python
"""Project model for tracked *craft repositories."""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from craft_dashboard.models.base import Base, TimestampMixin


class Project(TimestampMixin, Base):
    """A tracked *craft project (application, library, or other)."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    github_org: Mapped[str] = mapped_column(String(255), default="canonical")
    launchpad_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships (defined here, populated by back_populates in related models)
    issues: Mapped[list["Issue"]] = relationship(back_populates="project")  # noqa: F821
    snapshots: Mapped[list["Snapshot"]] = relationship(back_populates="project")  # noqa: F821
    releases: Mapped[list["Release"]] = relationship(back_populates="project")  # noqa: F821
    dependencies: Mapped[list["Dependency"]] = relationship(back_populates="project")  # noqa: F821

    def __repr__(self) -> str:
        """Return a string representation."""
        return f"<Project(name={self.name!r}, category={self.category!r})>"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/models/test_project.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/models/project.py tests/unit/models/test_project.py
git commit -m "feat: add Project model"
```

---

### Task 4: Issue Model

**Files:**
- Create: `craft_dashboard/models/issue.py`
- Test: `tests/unit/models/test_issue.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/models/test_issue.py`:
```python
"""Tests for the Issue model."""

from craft_dashboard.models.issue import Issue


class TestIssueModel:
    """Tests for the Issue model."""

    def test_tablename(self) -> None:
        """Issue model uses 'issues' table."""
        assert Issue.__tablename__ == "issues"

    def test_required_columns(self) -> None:
        """Issue model has all required columns."""
        column_names = {col.name for col in Issue.__table__.columns}
        expected = {
            "id",
            "project_id",
            "source",
            "external_id",
            "issue_type",
            "title",
            "body",
            "state",
            "author",
            "author_is_maintainer",
            "labels",
            "created_at",
            "updated_at",
            "closed_at",
            "url",
            "metadata",
            "last_fetched_at",
        }
        assert expected.issubset(column_names)

    def test_unique_constraint(self) -> None:
        """Issue has a unique constraint on (project_id, source, external_id)."""
        constraints = Issue.__table__.constraints
        unique_constraints = [
            c
            for c in constraints
            if hasattr(c, "columns")
            and {col.name for col in c.columns}
            == {"project_id", "source", "external_id"}
        ]
        assert len(unique_constraints) == 1

    def test_project_id_foreign_key(self) -> None:
        """project_id references projects.id."""
        col = Issue.__table__.columns["project_id"]
        fk_targets = [fk.target_fullname for fk in col.foreign_keys]
        assert "projects.id" in fk_targets
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/models/test_issue.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/models/issue.py`:
```python
"""Issue and Pull Request model."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from craft_dashboard.models.base import Base


class Issue(Base):
    """An issue or pull request from GitHub or Launchpad."""

    __tablename__ = "issues"
    __table_args__ = (
        UniqueConstraint("project_id", "source", "external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_is_maintainer: Mapped[bool] = mapped_column(Boolean, default=False)
    labels: Mapped[dict] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="issues")  # noqa: F821
    evaluations: Mapped[list["LLMEvaluation"]] = relationship(  # noqa: F821
        back_populates="issue",
        order_by="LLMEvaluation.evaluated_at.desc()",
    )

    @property
    def latest_evaluation(self) -> "LLMEvaluation | None":  # noqa: F821
        """Return the most recent LLM evaluation, or None."""
        return next((e for e in self.evaluations if e.latest), None)

    def __repr__(self) -> str:
        """Return a string representation."""
        return f"<Issue(source={self.source!r}, external_id={self.external_id!r}, title={self.title!r})>"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/models/test_issue.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/models/issue.py tests/unit/models/test_issue.py
git commit -m "feat: add Issue model for GitHub and Launchpad issues/PRs"
```

---

### Task 5: LLM Evaluation Model

**Files:**
- Create: `craft_dashboard/models/llm_evaluation.py`
- Test: `tests/unit/models/test_llm_evaluation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/models/test_llm_evaluation.py`:
```python
"""Tests for the LLMEvaluation model."""

from craft_dashboard.models.llm_evaluation import LLMEvaluation


class TestLLMEvaluationModel:
    """Tests for the LLMEvaluation model."""

    def test_tablename(self) -> None:
        """LLMEvaluation model uses 'llm_evaluations' table."""
        assert LLMEvaluation.__tablename__ == "llm_evaluations"

    def test_required_columns(self) -> None:
        """LLMEvaluation model has all required columns."""
        column_names = {col.name for col in LLMEvaluation.__table__.columns}
        expected = {
            "id",
            "issue_id",
            "model_name",
            "summary",
            "suggested_action",
            "suggested_action_reason",
            "scores",
            "tokens_used",
            "evaluated_at",
            "issue_data_hash",
            "latest",
        }
        assert expected.issubset(column_names)

    def test_partial_unique_index_on_latest(self) -> None:
        """A partial unique index enforces only one latest=true row per issue."""
        indexes = LLMEvaluation.__table__.indexes
        partial_unique = [
            idx for idx in indexes
            if idx.unique
            and {col.name for col in idx.columns} == {"issue_id"}
        ]
        assert len(partial_unique) == 1

    def test_issue_id_foreign_key(self) -> None:
        """issue_id references issues.id."""
        col = LLMEvaluation.__table__.columns["issue_id"]
        fk_targets = [fk.target_fullname for fk in col.foreign_keys]
        assert "issues.id" in fk_targets
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/models/test_llm_evaluation.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/models/llm_evaluation.py`:
```python
"""LLM evaluation model for issue/PR scoring."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from craft_dashboard.models.base import Base


class LLMEvaluation(Base):
    """An LLM-generated evaluation of an issue or pull request.

    Multiple evaluations can exist per issue (history is kept). The most
    recent evaluation has latest=True; all previous ones have latest=False.
    A partial unique index enforces that only one row per issue has latest=True.
    """

    __tablename__ = "llm_evaluations"
    __table_args__ = (
        # Only one 'latest' evaluation per issue at a time
        Index(
            "ix_llm_evaluations_latest_issue",
            "issue_id",
            unique=True,
            postgresql_where="latest = true",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("issues.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_action: Mapped[str | None] = mapped_column(String(50), nullable=True)
    suggested_action_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    scores: Mapped[dict] = mapped_column(JSONB, default=dict)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    issue_data_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    issue: Mapped["Issue"] = relationship(back_populates="evaluations")  # noqa: F821

    def __repr__(self) -> str:
        """Return a string representation."""
        return (
            f"<LLMEvaluation(issue_id={self.issue_id}, "
            f"action={self.suggested_action!r}, latest={self.latest})>"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/models/test_llm_evaluation.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/models/llm_evaluation.py tests/unit/models/test_llm_evaluation.py
git commit -m "feat: add LLMEvaluation model for issue scoring"
```

---

### Task 6: Snapshot Model

**Files:**
- Create: `craft_dashboard/models/snapshot.py`
- Test: `tests/unit/models/test_snapshot.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/models/test_snapshot.py`:
```python
"""Tests for the Snapshot model."""

from craft_dashboard.models.snapshot import Snapshot


class TestSnapshotModel:
    """Tests for the Snapshot model."""

    def test_tablename(self) -> None:
        """Snapshot model uses 'snapshots' table."""
        assert Snapshot.__tablename__ == "snapshots"

    def test_required_columns(self) -> None:
        """Snapshot model has all required columns."""
        column_names = {col.name for col in Snapshot.__table__.columns}
        expected = {
            "id",
            "project_id",
            "snapshot_date",
            "open_issues",
            "open_prs",
            "open_issues_external",
            "open_issues_internal",
            "open_prs_external",
            "open_prs_internal",
            "open_bugs",
        }
        assert expected.issubset(column_names)

    def test_unique_constraint(self) -> None:
        """Snapshot has a unique constraint on (project_id, snapshot_date)."""
        constraints = Snapshot.__table__.constraints
        unique_constraints = [
            c
            for c in constraints
            if hasattr(c, "columns")
            and {col.name for col in c.columns} == {"project_id", "snapshot_date"}
        ]
        assert len(unique_constraints) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/models/test_snapshot.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/models/snapshot.py`:
```python
"""Daily snapshot model for tracking issue/PR trends over time."""

from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from craft_dashboard.models.base import Base


class Snapshot(Base):
    """A daily snapshot of open issue and PR counts for a project."""

    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("project_id", "snapshot_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    open_issues: Mapped[int] = mapped_column(Integer, default=0)
    open_prs: Mapped[int] = mapped_column(Integer, default=0)
    open_issues_external: Mapped[int] = mapped_column(Integer, default=0)
    open_issues_internal: Mapped[int] = mapped_column(Integer, default=0)
    open_prs_external: Mapped[int] = mapped_column(Integer, default=0)
    open_prs_internal: Mapped[int] = mapped_column(Integer, default=0)
    open_bugs: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="snapshots")  # noqa: F821

    def __repr__(self) -> str:
        """Return a string representation."""
        return f"<Snapshot(project_id={self.project_id}, date={self.snapshot_date})>"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/models/test_snapshot.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/models/snapshot.py tests/unit/models/test_snapshot.py
git commit -m "feat: add Snapshot model for daily trend tracking"
```

---

### Task 7: Release Model

**Files:**
- Create: `craft_dashboard/models/release.py`
- Test: `tests/unit/models/test_release.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/models/test_release.py`:
```python
"""Tests for the Release model."""

from craft_dashboard.models.release import Release


class TestReleaseModel:
    """Tests for the Release model."""

    def test_tablename(self) -> None:
        """Release model uses 'releases' table."""
        assert Release.__tablename__ == "releases"

    def test_required_columns(self) -> None:
        """Release model has all required columns."""
        column_names = {col.name for col in Release.__table__.columns}
        expected = {
            "id",
            "project_id",
            "version",
            "branch",
            "released_at",
            "is_hotfix",
            "metadata",
        }
        assert expected.issubset(column_names)

    def test_unique_constraint(self) -> None:
        """Release has a unique constraint on (project_id, version)."""
        constraints = Release.__table__.constraints
        unique_constraints = [
            c
            for c in constraints
            if hasattr(c, "columns")
            and {col.name for col in c.columns} == {"project_id", "version"}
        ]
        assert len(unique_constraints) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/models/test_release.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/models/release.py`:
```python
"""Release model for tracking project versions."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from craft_dashboard.models.base import Base


class Release(Base):
    """A release version of a project."""

    __tablename__ = "releases"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_hotfix: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="releases")  # noqa: F821

    def __repr__(self) -> str:
        """Return a string representation."""
        return f"<Release(project_id={self.project_id}, version={self.version!r})>"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/models/test_release.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/models/release.py tests/unit/models/test_release.py
git commit -m "feat: add Release model"
```

---

### Task 8: Dependency Model

**Files:**
- Create: `craft_dashboard/models/dependency.py`
- Test: `tests/unit/models/test_dependency.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/models/test_dependency.py`:
```python
"""Tests for the Dependency model."""

from craft_dashboard.models.dependency import Dependency


class TestDependencyModel:
    """Tests for the Dependency model."""

    def test_tablename(self) -> None:
        """Dependency model uses 'dependencies' table."""
        assert Dependency.__tablename__ == "dependencies"

    def test_required_columns(self) -> None:
        """Dependency model has all required columns."""
        column_names = {col.name for col in Dependency.__table__.columns}
        expected = {
            "id",
            "project_id",
            "branch",
            "dependency_name",
            "version_spec",
            "source_file",
            "fetched_at",
        }
        assert expected.issubset(column_names)

    def test_unique_constraint(self) -> None:
        """Dependency has a unique constraint on (project_id, branch, dependency_name)."""
        constraints = Dependency.__table__.constraints
        unique_constraints = [
            c
            for c in constraints
            if hasattr(c, "columns")
            and {col.name for col in c.columns}
            == {"project_id", "branch", "dependency_name"}
        ]
        assert len(unique_constraints) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/models/test_dependency.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/models/dependency.py`:
```python
"""Dependency model for tracking project dependencies."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from craft_dashboard.models.base import Base


class Dependency(Base):
    """A dependency of a project on a specific branch."""

    __tablename__ = "dependencies"
    __table_args__ = (
        UniqueConstraint("project_id", "branch", "dependency_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    dependency_name: Mapped[str] = mapped_column(String(255), nullable=False)
    version_spec: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="dependencies")  # noqa: F821

    def __repr__(self) -> str:
        """Return a string representation."""
        return (
            f"<Dependency(project_id={self.project_id}, "
            f"name={self.dependency_name!r}, branch={self.branch!r})>"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/models/test_dependency.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/models/dependency.py tests/unit/models/test_dependency.py
git commit -m "feat: add Dependency model"
```

---

### Task 9: Refresh Schedule Model

**Files:**
- Create: `craft_dashboard/models/refresh_schedule.py`
- Test: `tests/unit/models/test_refresh_schedule.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/models/test_refresh_schedule.py`:
```python
"""Tests for the RefreshSchedule model."""

from craft_dashboard.models.refresh_schedule import RefreshSchedule


class TestRefreshScheduleModel:
    """Tests for the RefreshSchedule model."""

    def test_tablename(self) -> None:
        """RefreshSchedule model uses 'refresh_schedule' table."""
        assert RefreshSchedule.__tablename__ == "refresh_schedule"

    def test_required_columns(self) -> None:
        """RefreshSchedule model has all required columns."""
        column_names = {col.name for col in RefreshSchedule.__table__.columns}
        expected = {
            "id",
            "project_id",
            "source",
            "next_refresh_at",
            "last_refreshed_at",
            "last_error",
            "consecutive_failures",
        }
        assert expected.issubset(column_names)

    def test_unique_constraint(self) -> None:
        """RefreshSchedule has a unique constraint on (project_id, source)."""
        constraints = RefreshSchedule.__table__.constraints
        unique_constraints = [
            c
            for c in constraints
            if hasattr(c, "columns")
            and {col.name for col in c.columns} == {"project_id", "source"}
        ]
        assert len(unique_constraints) == 1

    def test_default_consecutive_failures(self) -> None:
        """consecutive_failures defaults to 0."""
        col = RefreshSchedule.__table__.columns["consecutive_failures"]
        assert col.default.arg == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/models/test_refresh_schedule.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/models/refresh_schedule.py`:
```python
"""Refresh schedule model for tracking data collection timing."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from craft_dashboard.models.base import Base


class RefreshSchedule(Base):
    """Schedule entry tracking when to next refresh data for a project+source.

    Also records error state so the admin view can highlight projects that
    are consistently failing to collect.
    """

    __tablename__ = "refresh_schedule"
    __table_args__ = (UniqueConstraint("project_id", "source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    next_refresh_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        """Return a string representation."""
        return (
            f"<RefreshSchedule(project_id={self.project_id}, "
            f"source={self.source!r}, failures={self.consecutive_failures})>"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/models/test_refresh_schedule.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/models/refresh_schedule.py tests/unit/models/test_refresh_schedule.py
git commit -m "feat: add RefreshSchedule model"
```

---

### Task 10: Update Models `__init__.py` and Set Up Alembic

**Files:**
- Modify: `craft_dashboard/models/__init__.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`

- [ ] **Step 1: Update `craft_dashboard/models/__init__.py` to export all models**

Replace `craft_dashboard/models/__init__.py` with:
```python
"""SQLAlchemy models for craft-dashboard."""

from craft_dashboard.models.base import Base
from craft_dashboard.models.dependency import Dependency
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.models.refresh_schedule import RefreshSchedule
from craft_dashboard.models.release import Release
from craft_dashboard.models.snapshot import Snapshot

__all__ = [
    "Base",
    "Dependency",
    "Issue",
    "LLMEvaluation",
    "Project",
    "RefreshSchedule",
    "Release",
    "Snapshot",
]
```

- [ ] **Step 2: Create `alembic.ini`**

```ini
[alembic]
script_location = alembic
prepend_sys_path = .

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 3: Create `alembic/env.py`**

```python
"""Alembic migration environment configuration."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from craft_dashboard.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Use synchronous URL for migrations (replace asyncpg with psycopg2)
database_url = os.environ.get(
    "DATABASE_URL", "postgresql://localhost/craft_dashboard"
)
sync_url = database_url.replace("+asyncpg", "")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=sync_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = create_engine(sync_url)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Create `alembic/script.py.mako`**

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 5: Create the `alembic/versions/` directory**

```bash
mkdir -p alembic/versions
touch alembic/versions/.gitkeep
```

- [ ] **Step 6: Commit**

```bash
git add craft_dashboard/models/__init__.py alembic.ini alembic/
git commit -m "feat: set up Alembic for database migrations"
```

---

### Task 11: FastAPI Database Dependency Injection

**Files:**
- Modify: `craft_dashboard/app.py`
- Create: `craft_dashboard/dependencies.py`
- Test: `tests/unit/test_dependencies.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dependencies.py`:
```python
"""Tests for FastAPI dependency injection helpers."""

from craft_dashboard.dependencies import get_db_session


class TestGetDbSession:
    """Tests for get_db_session."""

    def test_get_db_session_is_async_generator(self) -> None:
        """get_db_session is an async generator function."""
        import inspect

        assert inspect.isasyncgenfunction(get_db_session)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dependencies.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/dependencies.py`:
```python
"""FastAPI dependency injection helpers."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# This will be set during app startup
_session_factory: async_sessionmaker[AsyncSession] | None = None


def set_session_factory(factory: async_sessionmaker[AsyncSession]) -> None:
    """Set the global session factory (called during app startup).

    Args:
        factory: The async session factory to use.
    """
    global _session_factory  # noqa: PLW0603
    _session_factory = factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session for use in FastAPI route handlers.

    Yields:
        An AsyncSession that is automatically closed after the request.

    Raises:
        RuntimeError: If the session factory has not been initialized.
    """
    if _session_factory is None:
        msg = "Database session factory not initialized. Call set_session_factory first."
        raise RuntimeError(msg)

    async with _session_factory() as session:
        yield session
```

- [ ] **Step 4: Update `craft_dashboard/app.py` to wire up database on startup**

Replace `craft_dashboard/app.py` with:
```python
"""FastAPI application factory for craft-dashboard."""

import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.dependencies import set_session_factory
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

- [ ] **Step 5: Run all tests to verify nothing broke**

Run: `uv run pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add craft_dashboard/dependencies.py craft_dashboard/app.py tests/unit/test_dependencies.py
git commit -m "feat: add database dependency injection for FastAPI routes"
```

---

### Task 12: Run Full Test Suite and Lint

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
git commit -m "chore: lint and format pass"
```
