"""Badge color thresholds shared by the dashboard metric modules."""

from __future__ import annotations

TRIAGE_GREEN_COUNT_THRESHOLD = 5
TRIAGE_BACKLOG_RED_CAP = 25
TRIAGE_RED_RATIO_THRESHOLD = 0.20
RELEASE_RED_DAYS_THRESHOLD = 60
RELEASE_YELLOW_DAYS_THRESHOLD = 30


def compute_triage_badge_color(untriaged: int, total_open: int) -> str:
    """Return badge color for untriaged backlog.

    Rules:
    - If total_open <= 0 or untriaged <= 0: "neutral"
    - If untriaged < 5: "green" (projects with 1-4 untriaged issues are healthy)
    - If untriaged >= 25: "red" (unconditional backlog cap)
    - If ratio (untriaged / total_open) > 0.20: "red"
    - Otherwise: "yellow"
    """
    if total_open <= 0 or untriaged <= 0:
        return "neutral"
    if untriaged < TRIAGE_GREEN_COUNT_THRESHOLD:
        return "green"
    if untriaged >= TRIAGE_BACKLOG_RED_CAP:
        return "red"
    ratio = untriaged / total_open
    if ratio > TRIAGE_RED_RATIO_THRESHOLD:
        return "red"
    return "yellow"


def compute_release_badge_color(days_ago: int | None) -> str:
    """Return badge color for elapsed days since release."""
    if days_ago is None:
        return "neutral"
    if days_ago > RELEASE_RED_DAYS_THRESHOLD:
        return "red"
    if days_ago > RELEASE_YELLOW_DAYS_THRESHOLD:
        return "yellow"
    return "green"
