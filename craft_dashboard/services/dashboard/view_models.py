"""Build the homepage view models rendered by the dashboard template.

Every derived string the dashboard shows -- delta labels, tooltips, badge CSS
classes, formatted ages -- is computed here so the template only iterates and
prints. Badge *classification* is not repeated: the colour words come from
:mod:`craft_dashboard.services.dashboard.badges` via the metric modules, and
this module only maps a colour onto its CSS class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from craft_dashboard.models.views import (
    AttentionItemView,
    DashboardView,
    DeltaView,
    ProjectHealthView,
    QuickWinView,
    ReleaseSpotlightView,
    ThroughputCardView,
    UntriagedCardView,
    UntriagedRowView,
    VelocityCardView,
    VelocityMetricView,
    VolumeCardView,
    VolumeRowView,
)
from craft_dashboard.utils.formatting import EM_DASH, format_age_days, format_count

if TYPE_CHECKING:
    from craft_dashboard.services.dashboard.types import (
        AgingPR,
        AppReleaseSpotlight,
        HomepageMetrics,
        NeedsTriageIssue,
        ProjectHealthRow,
        PRVelocity,
        QuickWinIssue,
        ResolutionThroughput,
        UntriagedBacklog,
        VolumeStats,
    )

POSITIVE_CSS = "kpi-delta--positive"
NEGATIVE_CSS = "kpi-delta--negative"
NEUTRAL_CSS = ""

_BASELINE_NOTE = "the monthly average over the last 12 months"

AGING_PR_BADGE_CSS = "dash-pill dash-pill--red"
NEEDS_TRIAGE_BADGE_CSS = "dash-pill dash-pill--yellow"
QUICK_WIN_BADGE_CSS = "dash-pill dash-pill--purple"


def badge_css_class(color: str) -> str:
    """Return the pill CSS classes for a badge colour word."""
    return f"dash-pill dash-pill--{color}"


def _signed(value: int) -> str:
    """Return the value with an explicit plus sign when positive."""
    return f"+{value}" if value > 0 else str(value)


def _direction_word(value: int) -> str:
    """Return the comparison word used in baseline tooltips."""
    return "higher" if value > 0 else "lower"


def _signed_css_class(value: int, *, lower_is_better: bool) -> str:
    """Return the delta CSS class for a signed value."""
    if value == 0:
        return NEUTRAL_CSS
    improved = value < 0 if lower_is_better else value > 0
    return POSITIVE_CSS if improved else NEGATIVE_CSS


def build_duration_delta(delta: int | None, baseline: int | None) -> DeltaView | None:
    """Return the delta view for an age metric, or None when there is nothing to show."""
    if delta is None or delta == 0 or baseline is None:
        return None
    tooltip = (
        f"{abs(delta)} days {_direction_word(delta)} than the {baseline} day "
        f"baseline ({_BASELINE_NOTE})"
    )
    return DeltaView(
        text=f"({_signed(delta)}d)",
        css_class=_signed_css_class(delta, lower_is_better=True),
        tooltip=tooltip,
    )


def build_throughput_delta(delta: int | None, baseline: int | None) -> DeltaView | None:
    """Return the delta view for monthly resolution throughput."""
    if delta is None or delta == 0 or baseline is None:
        return None
    tooltip = (
        f"{abs(delta)} items {_direction_word(delta)} than the "
        f"{format_count(baseline)} item baseline ({_BASELINE_NOTE})"
    )
    return DeltaView(
        text=f"({_signed(delta)}/mo)",
        css_class=_signed_css_class(delta, lower_is_better=False),
        tooltip=tooltip,
    )


def build_net_change(net: int, opened: int, closed: int, noun: str) -> DeltaView:
    """Return the 30-day net-change view for an open backlog counter."""
    tooltip = (
        f"Net change in open {noun} over the last 30 days "
        f"({opened} opened \u2212 {closed} closed = {_signed(net)} net change)"
    )
    return DeltaView(
        text=f"{'+' if net > 0 else ''}{format_count(net)} in 30d",
        css_class=_signed_css_class(net, lower_is_better=True),
        tooltip=tooltip,
    )


def _velocity_metric(
    avg_age: int | None,
    delta: int | None,
    baseline: int | None,
    count_label: str,
    *,
    suffix: str = "",
) -> VelocityMetricView:
    """Build one PR-age row with its formatted age and optional delta."""
    display_age = (
        f"{format_age_days(avg_age, use_days=True)}{suffix}"
        if avg_age is not None
        else EM_DASH
    )
    return VelocityMetricView(
        display_age=display_age,
        count_label=count_label,
        delta=build_duration_delta(delta, baseline),
    )


def build_velocity_card(velocity: PRVelocity) -> VelocityCardView:
    """Build the PR age and response KPI card."""
    return VelocityCardView(
        contributor=_velocity_metric(
            velocity["contributor_avg_age"],
            velocity["contributor_avg_delta"],
            velocity["contributor_avg_baseline"],
            f"{velocity['contributor_count']} open",
        ),
        first_response=_velocity_metric(
            velocity["first_response_avg_days"],
            velocity["first_response_avg_delta"],
            velocity["first_response_avg_baseline"],
            f"{velocity['first_response_waiting_count']} unreviewed",
            suffix=" wait",
        ),
        overall=_velocity_metric(
            velocity["overall_avg_age"],
            velocity["overall_avg_delta"],
            velocity["overall_avg_baseline"],
            f"{velocity['overall_count']} open",
        ),
    )


def build_throughput_card(throughput: ResolutionThroughput) -> ThroughputCardView:
    """Build the resolution throughput KPI card."""
    return ThroughputCardView(
        total_30d=format_count(throughput["total_30d"]),
        issues_30d=format_count(throughput["issues_30d"]),
        prs_30d=format_count(throughput["prs_30d"]),
        total_365d=format_count(throughput["total_365d"]),
        issues_365d=format_count(throughput["issues_365d"]),
        prs_365d=format_count(throughput["prs_365d"]),
        delta=build_throughput_delta(
            throughput.get("total_monthly_delta"),
            throughput.get("total_monthly_avg"),
        ),
    )


def _untriaged_row(count: int, new_30d: int) -> UntriagedRowView:
    """Build one untriaged backlog row."""
    return UntriagedRowView(
        count=format_count(count),
        new_30d_text=f"+{new_30d}",
        new_30d_css_class=_signed_css_class(new_30d, lower_is_better=False),
    )


def build_untriaged_card(untriaged: UntriagedBacklog) -> UntriagedCardView:
    """Build the untriaged backlog KPI card."""
    return UntriagedCardView(
        issues=_untriaged_row(untriaged["issues_count"], untriaged["issues_30d_new"]),
        prs=_untriaged_row(untriaged["prs_count"], untriaged["prs_30d_new"]),
    )


def build_volume_card(volume: VolumeStats) -> VolumeCardView:
    """Build the all-time volume KPI card."""
    return VolumeCardView(
        issues=VolumeRowView(
            total=f"{format_count(volume['open_issues'])} open",
            closed=format_count(volume["closed_issues"]),
            net=build_net_change(
                volume["net_open_issues_30d"],
                volume["created_issues_30d"],
                volume["issues_30d_closed"],
                "issues",
            ),
        ),
        prs=VolumeRowView(
            total=f"{format_count(volume['open_prs'])} open",
            closed=format_count(volume["closed_prs"]),
            net=build_net_change(
                volume["net_open_prs_30d"],
                volume["created_prs_30d"],
                volume["prs_30d_closed"],
                "PRs",
            ),
        ),
        combined=VolumeRowView(
            total=format_count(volume["total_items"]),
            closed=None,
            net=build_net_change(
                volume["net_open_total_30d"],
                volume["created_total_30d"],
                volume["total_30d_closed"],
                "backlog",
            ),
        ),
    )


def build_release_spotlights(
    apps: list[AppReleaseSpotlight],
) -> list[ReleaseSpotlightView]:
    """Build the least-recent application release rows."""
    views: list[ReleaseSpotlightView] = []
    for app in apps:
        days_ago = app["days_ago"]
        released_at = app["released_at"]
        views.append(
            ReleaseSpotlightView(
                project_name=app["project_name"],
                version=app["version"],
                is_fallback=app["is_fallback"],
                released_display=(
                    released_at.strftime("%Y-%m-%d") if released_at else EM_DASH
                ),
                age_label=(
                    f"{format_age_days(days_ago)} ago" if days_ago is not None else None
                ),
                age_tooltip=(f"{days_ago} days ago" if days_ago is not None else None),
                badge_css_class=badge_css_class(app["badge_color"]),
            )
        )
    return views


def _attention_item(
    item: AgingPR | NeedsTriageIssue, badge_css: str
) -> AttentionItemView:
    """Build one aging-PR or needs-triage spotlight row."""
    return AttentionItemView(
        project_name=item["project_name"],
        external_id=item["external_id"],
        title=item["title"],
        author_display=item["author"] or EM_DASH,
        reference_label=f"{item['project_name']} #{item['external_id']}",
        display_age=format_age_days(item["days_old"]),
        age_tooltip=f"{item['days_old']} days",
        badge_css_class=badge_css,
    )


def build_aging_prs(items: list[AgingPR]) -> list[AttentionItemView]:
    """Build the aging contributor PR rows."""
    return [_attention_item(item, AGING_PR_BADGE_CSS) for item in items]


def build_needs_triage_issues(
    items: list[NeedsTriageIssue],
) -> list[AttentionItemView]:
    """Build the needs-triage issue rows."""
    return [_attention_item(item, NEEDS_TRIAGE_BADGE_CSS) for item in items]


def build_quick_wins(items: list[QuickWinIssue]) -> list[QuickWinView]:
    """Build the quick-win spotlight rows."""
    return [
        QuickWinView(
            project_name=item["project_name"],
            external_id=item["external_id"],
            title=item["title"],
            reference_label=f"{item['project_name']} #{item['external_id']}",
            score_label=f"Score {item['quick_win']}",
            badge_css_class=QUICK_WIN_BADGE_CSS,
        )
        for item in items
    ]


def build_project_health(rows: list[ProjectHealthRow]) -> list[ProjectHealthView]:
    """Build the project health and navigation rows."""
    views: list[ProjectHealthView] = []
    for row in rows:
        days_ago = row["latest_release_days_ago"]
        views.append(
            ProjectHealthView(
                name=row["name"],
                github_url=f"https://github.com/{row['github_org']}/{row['name']}",
                open_issues_label=f"{row['open_issues']} issues",
                open_prs_label=f"{row['open_prs']} PRs",
                show_prs=row["show_prs"],
                untriaged_label=(
                    f"{row['untriaged_count']} untriaged ({row['untriaged_pct']}%)"
                ),
                triage_badge_css_class=badge_css_class(row["triage_badge_color"]),
                show_release=row["show_release"],
                release_version=row["latest_release_version"],
                release_age_label=(
                    f"({format_age_days(days_ago)} ago)"
                    if days_ago is not None
                    else None
                ),
                release_age_tooltip=(
                    f"{days_ago} days ago" if days_ago is not None else None
                ),
                release_badge_css_class=badge_css_class(row["release_badge_color"]),
            )
        )
    return views


def build_dashboard_view(metrics: HomepageMetrics) -> DashboardView:
    """Convert the homepage metrics payload into its rendered view model."""
    return DashboardView(
        velocity=build_velocity_card(metrics["velocity"]),
        throughput=build_throughput_card(metrics["throughput"]),
        untriaged=build_untriaged_card(metrics["untriaged"]),
        volume=build_volume_card(metrics["volume"]),
        least_recent_apps=build_release_spotlights(metrics["least_recent_apps"]),
        aging_prs=build_aging_prs(metrics["aging_prs"]),
        needs_triage_issues=build_needs_triage_issues(metrics["needs_triage_issues"]),
        quick_wins=build_quick_wins(metrics["quick_wins"]),
        application_projects=build_project_health(metrics["application_projects"]),
        library_projects=build_project_health(metrics["library_projects"]),
        other_projects=build_project_health(metrics["other_projects"]),
    )
