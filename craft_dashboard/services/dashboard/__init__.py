"""Focused metric modules composing the dashboard service payloads."""

from craft_dashboard.services.dashboard.badges import (
    compute_release_badge_color,
    compute_triage_badge_color,
)

__all__ = [
    "compute_release_badge_color",
    "compute_triage_badge_color",
]
