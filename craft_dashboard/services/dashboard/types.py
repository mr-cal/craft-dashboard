"""Typed payloads returned by the dashboard metric modules."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from datetime import datetime


class PRVelocity(TypedDict):
    """PR Velocity and response metrics for contributor and overall open PRs."""

    contributor_count: int
    contributor_avg_age: int | None
    contributor_avg_delta: int | None
    contributor_avg_baseline: int | None
    first_response_waiting_count: int
    first_response_avg_days: int | None
    first_response_avg_delta: int | None
    first_response_avg_baseline: int | None
    overall_count: int
    overall_avg_age: int | None
    overall_avg_delta: int | None
    overall_avg_baseline: int | None


class ResolutionThroughput(TypedDict):
    """Closure throughput over 30 days and 365 days."""

    issues_30d: int
    prs_30d: int
    total_30d: int
    issues_365d: int
    prs_365d: int
    total_365d: int
    total_monthly_avg: int
    total_monthly_delta: int


class UntriagedBacklog(TypedDict):
    """Untriaged queues split for issues and pull requests."""

    issues_count: int
    issues_30d_new: int
    prs_count: int
    prs_30d_new: int


class VolumeStats(TypedDict):
    """All-time issues and PRs volume with trailing 30-day deltas."""

    open_issues: int
    closed_issues: int
    issues_30d_closed: int
    open_issues_30d: int
    created_issues_30d: int
    net_open_issues_30d: int
    open_prs: int
    closed_prs: int
    prs_30d_closed: int
    open_prs_30d: int
    created_prs_30d: int
    net_open_prs_30d: int
    total_items: int
    total_30d_closed: int
    open_total_30d: int
    created_total_30d: int
    net_open_total_30d: int


class AppReleaseSpotlight(TypedDict):
    """Application release status for least-recent release spotlight."""

    project_name: str
    version: str
    released_at: datetime | None
    days_ago: int | None
    badge_color: str
    is_fallback: bool


class AgingPR(TypedDict):
    """Aging open contributor PR item."""

    project_name: str
    external_id: str
    title: str
    author: str | None
    days_old: int
    url: str | None


class NeedsTriageIssue(TypedDict):
    """Needs triage issue item."""

    project_name: str
    external_id: str
    title: str
    author: str | None
    days_old: int
    url: str | None


class QuickWinIssue(TypedDict):
    """High-impact quick win issue item."""

    project_name: str
    external_id: str
    title: str
    quick_win: int
    impact: float | None
    complexity: float | None
    url: str | None


class ProjectHealthRow(TypedDict):
    """Project health metrics row with navigation shortcuts."""

    name: str
    github_org: str
    category: str
    open_issues: int
    open_prs: int
    show_prs: bool
    untriaged_count: int
    untriaged_pct: float
    triage_badge_color: str
    latest_release_version: str | None
    latest_release_days_ago: int | None
    release_badge_color: str
    show_release: bool


class RepoCadenceRow(TypedDict):
    """Cadence row for all tracked repositories."""

    name: str
    full_name: str
    github_org: str
    category: str
    latest_version: str
    released_at: datetime | None
    released_at_str: str
    days_ago: int | None
    badge_color: str
    commits_since: int | None
    is_fallback: bool


class ContributorAwaitingPR(TypedDict):
    """Contributor PR awaiting maintainer review or response."""

    project_name: str
    external_id: str
    title: str
    author: str | None
    days_waiting: int
    url: str | None
    created_at_str: str


class ProjectTriageHealthRow(TypedDict):
    """Project-level triage queue status."""

    name: str
    github_org: str
    category: str
    open_issues: int
    open_prs: int
    untriaged_issues: int
    untriaged_prs: int
    untriaged_total: int
    untriaged_pct: float
    badge_color: str


class TriageResponsivenessData(TypedDict):
    """Data payload for Triage & Responsiveness page."""

    velocity: PRVelocity
    awaiting_prs: list[ContributorAwaitingPR]
    application_projects: list[ProjectTriageHealthRow]
    library_projects: list[ProjectTriageHealthRow]
    other_projects: list[ProjectTriageHealthRow]
    action_counts: dict[str, int]
    total_open: int
    evaluated_count: int
    healthy_project_count: int
    attention_project_count: int
    overdue_project_count: int


class HomepageMetrics(TypedDict):
    """Aggregated homepage metrics payload."""

    project_count: int
    velocity: PRVelocity
    throughput: ResolutionThroughput
    untriaged: UntriagedBacklog
    volume: VolumeStats
    least_recent_apps: list[AppReleaseSpotlight]
    aging_prs: list[AgingPR]
    needs_triage_issues: list[NeedsTriageIssue]
    quick_wins: list[QuickWinIssue]
    application_projects: list[ProjectHealthRow]
    library_projects: list[ProjectHealthRow]
    other_projects: list[ProjectHealthRow]
