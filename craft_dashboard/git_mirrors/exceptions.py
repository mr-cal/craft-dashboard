"""Exceptions raised by the git mirror sandbox layer."""


class GitMirrorError(Exception):
    """Base class for git-mirror-layer errors."""


class UnknownProjectError(GitMirrorError):
    """Raised when a project name is not in the configured allowlist."""


class InvalidRefError(GitMirrorError):
    """Raised when a ref is not a 40-character hex SHA."""


class InvalidPathError(GitMirrorError):
    """Raised when a path is absolute or attempts directory traversal."""


class MirrorNotFoundError(GitMirrorError):
    """Raised when a project is allowlisted but has no mirror on disk yet."""
