"""Data collectors for external sources."""


class CollectorError(Exception):
    """Base exception for collector errors."""


class RateLimitError(CollectorError):
    """Raised when a GitHub API rate limit is exhausted and cannot be waited out."""

    def __init__(self, resource: str, remaining: int, limit: int) -> None:
        self.resource = resource
        self.remaining = remaining
        self.limit = limit
        super().__init__(
            f"{resource} rate limit exhausted: {remaining}/{limit} remaining"
        )


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
