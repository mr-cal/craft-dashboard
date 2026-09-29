"""Tests for the dashboard view models and badge classification boundaries."""

from datetime import UTC, datetime

import pytest
from craft_dashboard.services.dashboard.badges import (
    compute_release_badge_color,
    compute_triage_badge_color,
)
from craft_dashboard.services.dashboard.view_models import (
    NEGATIVE_CSS,
    NEUTRAL_CSS,
    POSITIVE_CSS,
    badge_css_class,
    build_aging_prs,
    build_dashboard_view,
    build_duration_delta,
    build_needs_triage_issues,
    build_net_change,
    build_project_health,
    build_quick_wins,
    build_release_spotlights,
    build_throughput_delta,
    build_untriaged_card,
    build_velocity_card,
)
from craft_dashboard.utils.formatting import format_age_days, format_count

BASELINE_NOTE = "the monthly average over the last 12 months"


class TestDurationDelta:
    """Delta rendering for age metrics, where lower is better."""

    def test_none_delta_is_hidden(self) -> None:
        assert build_duration_delta(None, 10) is None

    def test_zero_delta_is_hidden(self) -> None:
        assert build_duration_delta(0, 10) is None

    def test_missing_baseline_is_hidden(self) -> None:
        assert build_duration_delta(5, None) is None

    def test_positive_delta_is_negative_styling(self) -> None:
        delta = build_duration_delta(3, 39)

        assert delta is not None
        assert delta.text == "(+3d)"
        assert delta.css_class == NEGATIVE_CSS
        assert delta.tooltip == (
            f"3 days higher than the 39 day baseline ({BASELINE_NOTE})"
        )

    def test_negative_delta_is_positive_styling(self) -> None:
        delta = build_duration_delta(-4, 39)

        assert delta is not None
        assert delta.text == "(-4d)"
        assert delta.css_class == POSITIVE_CSS
        assert delta.tooltip == (
            f"4 days lower than the 39 day baseline ({BASELINE_NOTE})"
        )


class TestThroughputDelta:
    """Delta rendering for throughput, where higher is better."""

    @pytest.mark.parametrize(
        ("delta", "baseline"),
        [(None, 100), (0, 100), (5, None)],
    )
    def test_hidden_cases(self, delta: int | None, baseline: int | None) -> None:
        assert build_throughput_delta(delta, baseline) is None

    def test_positive_delta_is_positive_styling(self) -> None:
        view = build_throughput_delta(257, 1283)

        assert view is not None
        assert view.text == "(+257/mo)"
        assert view.css_class == POSITIVE_CSS
        assert view.tooltip == (
            f"257 items higher than the 1,283 item baseline ({BASELINE_NOTE})"
        )

    def test_negative_delta_is_negative_styling(self) -> None:
        view = build_throughput_delta(-12, 1283)

        assert view is not None
        assert view.text == "(-12/mo)"
        assert view.css_class == NEGATIVE_CSS
        assert "12 items lower than the 1,283 item baseline" in view.tooltip


class TestNetChange:
    """Net open-backlog change, where a shrinking backlog is good."""

    def test_growing_backlog_is_negative_styling(self) -> None:
        view = build_net_change(1200, 1500, 300, "issues")

        assert view.text == "+1,200 in 30d"
        assert view.css_class == NEGATIVE_CSS
        assert view.tooltip == (
            "Net change in open issues over the last 30 days "
            "(1500 opened \u2212 300 closed = +1200 net change)"
        )

    def test_shrinking_backlog_is_positive_styling(self) -> None:
        view = build_net_change(-20, 80, 100, "PRs")

        assert view.text == "-20 in 30d"
        assert view.css_class == POSITIVE_CSS
        assert "80 opened \u2212 100 closed = -20 net change" in view.tooltip

    def test_zero_is_neutral(self) -> None:
        view = build_net_change(0, 100, 100, "backlog")

        assert view.text == "0 in 30d"
        assert view.css_class == NEUTRAL_CSS


def _velocity(**overrides: object) -> dict:
    base = {
        "contributor_count": 12,
        "contributor_avg_age": 42,
        "contributor_avg_delta": 3,
        "contributor_avg_baseline": 39,
        "first_response_waiting_count": 5,
        "first_response_avg_days": 400,
        "first_response_avg_delta": None,
        "first_response_avg_baseline": None,
        "overall_count": 30,
        "overall_avg_age": None,
        "overall_avg_delta": None,
        "overall_avg_baseline": None,
    }
    base.update(overrides)
    return base


class TestVelocityCard:
    """Display ages and counts on the PR age card."""

    def test_display_age_uses_long_day_units(self) -> None:
        card = build_velocity_card(_velocity())

        assert card.contributor.display_age == "42 days"
        assert card.contributor.count_label == "12 open"

    def test_first_response_appends_wait(self) -> None:
        card = build_velocity_card(_velocity())

        assert card.first_response.display_age == "1.1 years wait"
        assert card.first_response.count_label == "5 unreviewed"
        assert card.first_response.delta is None

    def test_missing_age_renders_em_dash(self) -> None:
        card = build_velocity_card(_velocity())

        assert card.overall.display_age == "—"

    def test_delta_is_attached(self) -> None:
        card = build_velocity_card(_velocity())

        assert card.contributor.delta is not None
        assert card.contributor.delta.text == "(+3d)"


class TestUntriagedCard:
    """New-in-30-days styling on the untriaged card."""

    @pytest.mark.parametrize(
        ("new_30d", "expected"),
        [(7, POSITIVE_CSS), (-7, NEGATIVE_CSS), (0, NEUTRAL_CSS)],
    )
    def test_new_30d_css_class(self, new_30d: int, expected: str) -> None:
        card = build_untriaged_card(
            {
                "issues_count": 1234,
                "issues_30d_new": new_30d,
                "prs_count": 5,
                "prs_30d_new": 0,
            }
        )

        assert card.issues.new_30d_css_class == expected
        assert card.issues.new_30d_text == f"+{new_30d}"
        assert card.issues.count == "1,234"


class TestSpotlightViews:
    """Release, aging, triage, and quick-win spotlight rows."""

    def test_release_spotlight_formats_age_and_badge(self) -> None:
        [view] = build_release_spotlights(
            [
                {
                    "project_name": "snapcraft",
                    "version": "8.0.0",
                    "released_at": datetime(2024, 1, 2, tzinfo=UTC),
                    "days_ago": 400,
                    "badge_color": "red",
                    "is_fallback": False,
                }
            ]
        )

        assert view.released_display == "2024-01-02"
        assert view.age_label == "1.1y ago"
        assert view.age_tooltip == "400 days ago"
        assert view.badge_css_class == "dash-pill dash-pill--red"

    def test_release_spotlight_without_release(self) -> None:
        [view] = build_release_spotlights(
            [
                {
                    "project_name": "rockcraft",
                    "version": "(unreleased)",
                    "released_at": None,
                    "days_ago": None,
                    "badge_color": "neutral",
                    "is_fallback": True,
                }
            ]
        )

        assert view.released_display == "—"
        assert view.age_label is None
        assert view.age_tooltip is None

    def test_aging_pr_row(self) -> None:
        [view] = build_aging_prs(
            [
                {
                    "project_name": "charmcraft",
                    "external_id": "12",
                    "title": "Fix it",
                    "author": None,
                    "days_old": 30,
                    "url": None,
                }
            ]
        )

        assert view.author_display == "—"
        assert view.reference_label == "charmcraft #12"
        assert view.display_age == "30d"
        assert view.age_tooltip == "30 days"
        assert view.badge_css_class == "dash-pill dash-pill--red"

    def test_needs_triage_row_uses_yellow_badge(self) -> None:
        [view] = build_needs_triage_issues(
            [
                {
                    "project_name": "snapcraft",
                    "external_id": "77",
                    "title": "Crash",
                    "author": "bob",
                    "days_old": 7,
                    "url": None,
                }
            ]
        )

        assert view.author_display == "bob"
        assert view.badge_css_class == "dash-pill dash-pill--yellow"

    def test_quick_win_row(self) -> None:
        [view] = build_quick_wins(
            [
                {
                    "project_name": "snapcraft",
                    "external_id": "78",
                    "title": "Typo",
                    "quick_win": 91,
                    "impact": 0.5,
                    "complexity": 0.1,
                    "url": None,
                }
            ]
        )

        assert view.score_label == "Score 91"
        assert view.badge_css_class == "dash-pill dash-pill--purple"


def _health_row(**overrides: object) -> dict:
    base = {
        "name": "snapcraft",
        "github_org": "canonical",
        "category": "application",
        "open_issues": 10,
        "open_prs": 4,
        "show_prs": True,
        "untriaged_count": 3,
        "untriaged_pct": 21.4,
        "triage_badge_color": "green",
        "latest_release_version": "8.0.0",
        "latest_release_days_ago": 400,
        "release_badge_color": "red",
        "show_release": True,
    }
    base.update(overrides)
    return base


class TestProjectHealthView:
    """Project health rows carry fully rendered labels and badge classes."""

    def test_labels_and_links(self) -> None:
        [view] = build_project_health([_health_row()])

        assert view.github_url == "https://github.com/canonical/snapcraft"
        assert view.open_issues_label == "10 issues"
        assert view.open_prs_label == "4 PRs"
        assert view.untriaged_label == "3 untriaged (21.4%)"
        assert view.triage_badge_css_class == "dash-pill dash-pill--green"
        assert view.release_age_label == "(1.1y ago)"
        assert view.release_age_tooltip == "400 days ago"
        assert view.release_badge_css_class == "dash-pill dash-pill--red"

    def test_missing_release_age(self) -> None:
        [view] = build_project_health(
            [_health_row(latest_release_days_ago=None, release_badge_color="neutral")]
        )

        assert view.release_age_label is None
        assert view.release_age_tooltip is None
        assert view.release_badge_css_class == "dash-pill dash-pill--neutral"


class TestTriageBadgeBoundaries:
    """Every threshold boundary of the shared triage classification."""

    @pytest.mark.parametrize(
        ("untriaged", "total_open", "expected"),
        [
            (0, 100, "neutral"),
            (5, 0, "neutral"),
            (1, 100, "green"),
            (4, 100, "green"),
            (5, 100, "yellow"),
            (20, 100, "yellow"),
            (21, 100, "red"),
            (24, 100, "red"),
            (25, 1000, "red"),
            (24, 1000, "yellow"),
        ],
    )
    def test_boundaries(self, untriaged: int, total_open: int, expected: str) -> None:
        assert compute_triage_badge_color(untriaged, total_open) == expected


class TestReleaseBadgeBoundaries:
    """Every threshold boundary of the shared release classification."""

    @pytest.mark.parametrize(
        ("days_ago", "expected"),
        [
            (None, "neutral"),
            (0, "green"),
            (30, "green"),
            (31, "yellow"),
            (60, "yellow"),
            (61, "red"),
        ],
    )
    def test_boundaries(self, days_ago: int | None, expected: str) -> None:
        assert compute_release_badge_color(days_ago) == expected

    def test_badge_css_class_wraps_colour(self) -> None:
        assert badge_css_class("yellow") == "dash-pill dash-pill--yellow"


class TestFormatting:
    """Shared display formatting helpers."""

    @pytest.mark.parametrize(
        ("days", "expected"),
        [
            (None, "—"),
            ("nonsense", "—"),
            (0, "0d"),
            (364, "364d"),
            (365, "1y"),
            (882, "2.4y"),
        ],
    )
    def test_format_age_days(self, days: object, expected: str) -> None:
        assert format_age_days(days) == expected

    @pytest.mark.parametrize(
        ("days", "expected"),
        [(0, "0 days"), (365, "1 years"), (882, "2.4 years")],
    )
    def test_format_age_days_long_form(self, days: int, expected: str) -> None:
        assert format_age_days(days, use_days=True) == expected

    def test_format_count(self) -> None:
        assert format_count(1234567) == "1,234,567"


class TestBuildDashboardView:
    """The whole-page assembly wires each card and list."""

    def test_builds_every_section(self) -> None:
        metrics = {
            "project_count": 1,
            "velocity": _velocity(),
            "throughput": {
                "issues_30d": 10,
                "prs_30d": 5,
                "total_30d": 15,
                "issues_365d": 100,
                "prs_365d": 50,
                "total_365d": 1500,
                "total_monthly_avg": 12,
                "total_monthly_delta": 3,
            },
            "untriaged": {
                "issues_count": 4,
                "issues_30d_new": 1,
                "prs_count": 2,
                "prs_30d_new": 0,
            },
            "volume": {
                "open_issues": 10,
                "closed_issues": 20,
                "issues_30d_closed": 3,
                "open_issues_30d": 1,
                "created_issues_30d": 4,
                "net_open_issues_30d": 1,
                "open_prs": 5,
                "closed_prs": 6,
                "prs_30d_closed": 2,
                "open_prs_30d": 1,
                "created_prs_30d": 1,
                "net_open_prs_30d": -1,
                "total_items": 41,
                "total_30d_closed": 5,
                "open_total_30d": 2,
                "created_total_30d": 5,
                "net_open_total_30d": 0,
            },
            "least_recent_apps": [],
            "aging_prs": [],
            "needs_triage_issues": [],
            "quick_wins": [],
            "application_projects": [_health_row()],
            "library_projects": [],
            "other_projects": [],
        }

        view = build_dashboard_view(metrics)

        assert view.throughput.total_365d == "1,500"
        assert view.throughput.delta is not None
        assert view.volume.combined.net.css_class == NEUTRAL_CSS
        assert view.volume.combined.closed is None
        assert view.volume.issues.total == "10 open"
        assert len(view.application_projects) == 1
        assert view.least_recent_apps == []
