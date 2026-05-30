"""Read-only view models for templates and query results."""

from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class IssueView:
    """Read-only view of an issue with its evaluation data."""

    id: int
    project_name: str
    source: str
    external_id: str
    title: str
    author: str | None
    issue_type: str
    state: str
    url: str | None
    summary: str | None
    suggested_action: str | None
    suggested_action_reason: str | None
    scores: dict[str, float | None] = field(default_factory=dict)
    age_days: int | None = None
    labels: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    author_is_maintainer: bool = False
    author_is_bot: bool = False
    staleness: float | None = None
    duplicateness: float | None = None
    complexity: float | None = None
    support_request: float | None = None
    readiness: float | None = None

    def as_dict(self) -> dict[str, object]:
        """Return a dict representation for template compatibility."""
        return asdict(self)

    def get(self, key: str, default: object = None) -> object:
        """Provide dict-like access for templates."""
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> object:
        """Support dict-style access for existing callers."""
        return getattr(self, key)

    def __contains__(self, key: object) -> bool:
        """Support membership checks for field names."""
        return isinstance(key, str) and hasattr(self, key)


@dataclass(frozen=True)
class IssueFilters:
    """Filter parameters for issue queries."""

    project: str = ""
    source: str = ""
    state: str = "open"
    issue_type: str = ""
    action: str = ""
    author_role: str = ""
    sort_by: str = "staleness"
    page: int = 1
    search: str = ""
    items_per_page: int = 100
    llm_status: str = ""


@dataclass(frozen=True)
class IssueQueryResult:
    """Result from an issue query."""

    issues: list[IssueView]
    total_count: int
    total_pages: int
    page: int
