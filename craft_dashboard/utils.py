"""Shared utility functions."""

from datetime import UTC, datetime


def normalize_datetime(
    value: datetime | None, fallback: datetime | None = None
) -> datetime:
    """Return a timezone-aware datetime, defaulting missing values to fallback.

    Args:
        value: A datetime that may or may not be timezone-aware.
        fallback: Default datetime if value is None.

    Returns:
        A timezone-aware datetime.

    """
    if value is None:
        if fallback is None:
            return datetime.now(tz=UTC)
        return fallback
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
