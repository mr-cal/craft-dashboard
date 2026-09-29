"""Focused metric modules composing the dashboard service payloads."""

from craft_dashboard.services.dashboard.badges import (
    compute_release_badge_color,
    compute_triage_badge_color,
)
from craft_dashboard.services.dashboard.view_models import build_dashboard_view

__all__ = [
    "build_dashboard_view",
    "compute_release_badge_color",
    "compute_triage_badge_color",
]
