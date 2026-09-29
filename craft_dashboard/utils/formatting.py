"""Presentation formatting helpers shared by templates and view models."""

from __future__ import annotations

_DAYS_PER_YEAR = 365.0
EM_DASH = "—"


def format_age_days(days: float | None, *, use_days: bool = False) -> str:
    """Format elapsed days into human-readable compact string (e.g. '42d', '1.2y', '10.4y')."""
    if days is None:
        return EM_DASH
    try:
        d = float(days)
    except (ValueError, TypeError):
        return EM_DASH
    if d < _DAYS_PER_YEAR:
        suffix = " days" if use_days else "d"
        return f"{round(d)}{suffix}"
    years = round(d / _DAYS_PER_YEAR, 1)
    if years == int(years):
        return f"{int(years)} years" if use_days else f"{int(years)}y"
    return f"{years} years" if use_days else f"{years}y"


def format_count(value: int) -> str:
    """Format an integer with thousands separators."""
    return f"{value:,}"
