"""Collection run statistics and error summarization."""

from dataclasses import dataclass, field
from typing import Any

from github import GithubException


@dataclass
class CollectionStats:
    """Collection summary for logging."""

    projects_processed: set[str] = field(default_factory=set)
    issues_collected: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def merge(self, other: "CollectionStats") -> None:
        """Merge another collection summary into this one."""
        self.projects_processed.update(other.projects_processed)
        self.issues_collected += other.issues_collected
        self.errors.extend(other.errors)


def _format_duration(seconds: float) -> str:
    """Format elapsed seconds for human-readable logs."""
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if hours or minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


_MAX_ERROR_MESSAGE_LENGTH = 500


def _summarize_exception(exc: Exception) -> str:
    """Return a concise, human-readable error message for stats/db storage.

    ``GithubException.__str__`` dumps the *entire* API response body as
    JSON (see PyGithub's ``GithubException``), which for GraphQL errors can
    include the whole partial ``data`` payload repeated once per affected
    node — multi-megabyte error strings that make the admin status page's
    errors column unreadable. Use the concise ``status``/``message`` for
    ``GithubException`` (``github_graphql`` already summarizes GraphQL
    errors before raising), and hard-cap any other exception's message as a
    safety net against similarly oversized errors from other sources.
    """
    if isinstance(exc, GithubException):
        summary = f"{exc.status}"
        if exc.message:
            summary += f" {exc.message}"
        return summary
    text = str(exc)
    if len(text) > _MAX_ERROR_MESSAGE_LENGTH:
        text = f"{text[:_MAX_ERROR_MESSAGE_LENGTH]}... [truncated, {len(text)} chars total]"
    return text
