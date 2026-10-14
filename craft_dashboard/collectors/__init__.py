"""Data collectors for external sources."""

from datetime import datetime


class CollectorError(Exception):
    """Base exception for collector errors."""


class RateLimitError(CollectorError):
    """Raised when a GitHub API rate limit is exhausted and cannot be waited out."""

    def __init__(
        self,
        resource: str,
        remaining: int,
        limit: int,
        reset_at: datetime | None = None,
    ) -> None:
        self.resource = resource
        self.remaining = remaining
        self.limit = limit
        self.reset_at = reset_at
        msg = f"{resource} rate limit exhausted: {remaining}/{limit} remaining"
        if reset_at is not None:
            msg += f" (resets at {reset_at.isoformat()})"
        super().__init__(msg)


class DataValidationError(CollectorError):
    """Raised when collected data fails validation."""


# Fields updated during issue upserts (on_conflict_do_update set_ keys).
ISSUE_UPSERT_FIELDS = [
    "title",
    "body",
    "state",
    "author",
    "author_is_maintainer",
    "author_is_bot",
    "labels",
    "updated_at",
    "closed_at",
    "content_hash",
    "last_fetched_at",
    "collection_run_id",
]
