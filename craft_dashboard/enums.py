"""Enumerations used across craft-dashboard."""

from enum import StrEnum


class IssueState(StrEnum):
    """Normalized issue states."""

    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class IssueType(StrEnum):
    """Issue type classification."""

    ISSUE = "issue"
    PULL_REQUEST = "pull_request"


class IssueSource(StrEnum):
    """Data sources for issues."""

    GITHUB = "github"
    LAUNCHPAD = "launchpad"
