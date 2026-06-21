"""Data collectors for external sources."""


class CollectorError(Exception):
    """Base exception for collector errors."""


class RateLimitError(CollectorError):
    """Raised when an API rate limit is hit."""


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
    "last_fetched_at",
    "collection_run_id",
]
