"""Read-only view models for templates and query results."""

from dataclasses import asdict, dataclass, field
from datetime import date, datetime


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
    actionability: float | None = None
    complexity: float | None = None
    impact: float | None = None
    quick_win: int | None = None
    confidence: float | None = None
    has_related_links: bool = False

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
    sort_by: str = "impact"
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


@dataclass(frozen=True)
class DeltaView:
    """A metric delta rendered against its baseline.

    ``css_class`` is empty when the delta is neutral, which keeps the template
    free of any comparison against zero.
    """

    text: str
    css_class: str
    tooltip: str


@dataclass(frozen=True)
class VelocityMetricView:
    """One row of the PR age and response card."""

    display_age: str
    count_label: str
    delta: DeltaView | None


@dataclass(frozen=True)
class VelocityCardView:
    """PR age and response KPI card."""

    contributor: VelocityMetricView
    first_response: VelocityMetricView
    overall: VelocityMetricView


@dataclass(frozen=True)
class ThroughputCardView:
    """Resolution throughput KPI card."""

    total_30d: str
    issues_30d: str
    prs_30d: str
    total_365d: str
    issues_365d: str
    prs_365d: str
    delta: DeltaView | None


@dataclass(frozen=True)
class UntriagedRowView:
    """One row of the untriaged backlog card."""

    count: str
    new_30d_text: str
    new_30d_css_class: str


@dataclass(frozen=True)
class UntriagedCardView:
    """Untriaged backlog KPI card."""

    issues: UntriagedRowView
    prs: UntriagedRowView


@dataclass(frozen=True)
class VolumeRowView:
    """One row of the all-time volume card."""

    total: str
    closed: str | None
    net: DeltaView


@dataclass(frozen=True)
class VolumeCardView:
    """All-time volume KPI card."""

    issues: VolumeRowView
    prs: VolumeRowView
    combined: VolumeRowView


@dataclass(frozen=True)
class ReleaseSpotlightView:
    """A row of the least-recent application releases spotlight."""

    project_name: str
    version: str
    is_fallback: bool
    released_display: str
    age_label: str | None
    age_tooltip: str | None
    badge_css_class: str


@dataclass(frozen=True)
class AttentionItemView:
    """A row of the aging-PR or needs-triage spotlight tables."""

    project_name: str
    external_id: str
    title: str
    author_display: str
    reference_label: str
    display_age: str
    age_tooltip: str
    badge_css_class: str


@dataclass(frozen=True)
class QuickWinView:
    """A row of the quick-wins spotlight table."""

    project_name: str
    external_id: str
    title: str
    reference_label: str
    score_label: str
    badge_css_class: str


@dataclass(frozen=True)
class ProjectHealthView:
    """A row of the project health and navigation table."""

    name: str
    github_url: str
    open_issues_label: str
    open_prs_label: str
    show_prs: bool
    untriaged_label: str
    triage_badge_css_class: str
    show_release: bool
    release_version: str | None
    release_age_label: str | None
    release_age_tooltip: str | None
    release_badge_css_class: str


@dataclass(frozen=True)
class DashboardView:
    """Everything the dashboard template renders, fully precomputed."""

    velocity: VelocityCardView
    throughput: ThroughputCardView
    untriaged: UntriagedCardView
    volume: VolumeCardView
    least_recent_apps: list[ReleaseSpotlightView]
    aging_prs: list[AttentionItemView]
    needs_triage_issues: list[AttentionItemView]
    quick_wins: list[QuickWinView]
    application_projects: list[ProjectHealthView]
    library_projects: list[ProjectHealthView]
    other_projects: list[ProjectHealthView]


@dataclass(frozen=True)
class CIFilters:
    """Filter parameters for the CI failure catalog query."""

    project: str = ""
    workflow: str = ""
    branch_class: str = ""
    test_type: str = ""
    search: str = ""
    sort_by: str = "fail_rate"
    page: int = 1
    items_per_page: int = 100


# A signature's fail rate shows as "insufficient data" below this many
# observed runs of its job/workflow, so a single unlucky run never displays
# as "100% failing" (see plans/44-ci-failure-catalog.md, Design Decisions).
MIN_RUNS_FOR_FAIL_RATE = 10


@dataclass(frozen=True)
class CIFailureRow:
    """One aggregated row in the CI failure catalog (a signature, rolled up)."""

    project_name: str
    workflow_name: str
    job_name: str
    job_variant: str | None
    test_type: str
    signature: str
    branch_class: str
    occurrence_count: int
    total_count: int
    flip_flop_count: int
    is_environmental: bool
    last_seen_at: datetime | None
    sample_error_message: str | None

    @property
    def fail_rate(self) -> float | None:
        """Return occurrences/total, or None below the minimum sample size."""
        if self.total_count < MIN_RUNS_FOR_FAIL_RATE:
            return None
        return self.occurrence_count / self.total_count


@dataclass(frozen=True)
class CIQueryResult:
    """Result from a CI failure catalog query."""

    rows: list[CIFailureRow]
    total_count: int
    total_pages: int
    page: int


@dataclass(frozen=True)
class CIDailyPoint:
    """One day's rolled-up occurrence/total counts for a signature."""

    day: date
    fail_count: int
    total_count: int


@dataclass(frozen=True)
class CIRunOccurrence:
    """One individual run within the detail-retention window for a signature."""

    project_name: str
    workflow_name: str
    run_id: int
    head_branch: str | None
    branch_class: str
    html_url: str | None
    created_at: datetime | None
    error_message: str | None


@dataclass(frozen=True)
class CISignatureDetail:
    """Drill-down payload for a single signature: trend + recent linked runs."""

    signature: str
    test_type: str
    job_name: str
    job_variant: str | None
    is_environmental: bool
    daily_trend: list[CIDailyPoint]
    recent_runs: list[CIRunOccurrence]

    @property
    def sample_error_message(self) -> str | None:
        """Return the most recent recorded error message.

        Returns None if all runs in the 60-day detail window were cleared
        before one was ever recorded.

        A bare signature like ``.github:98`` is meaningless without the text
        that produced it, so this is shown right next to the signature
        itself rather than requiring a scroll to the recent-runs table.
        """
        return next(
            (run.error_message for run in self.recent_runs if run.error_message),
            None,
        )
