"""Shared datetime and coercion helpers for dashboard metrics."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime

type ReleaseRow = Any
"""One row of the latest-release query; SQLAlchemy types its columns as Any."""


def to_utc(value: datetime) -> datetime:
    """Return the value as a UTC-aware datetime, assuming naive values are UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def to_utc_or_none(value: datetime | None) -> datetime | None:
    """Return a UTC-aware datetime, or None when the value is missing."""
    return None if value is None else to_utc(value)


def days_since(now: datetime, value: datetime) -> int:
    """Return whole days elapsed between the value and now, floored at zero."""
    return max(0, (now - to_utc(value)).days)


def int_or_zero(val: object) -> int:
    """Safely convert a value to int, defaulting to 0."""
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    if isinstance(val, str):
        try:
            return int(val)
        except ValueError:
            return 0
    return 0
