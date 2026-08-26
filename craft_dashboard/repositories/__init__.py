"""Repository layer for database access."""

from craft_dashboard.repositories.issue_link_repository import IssueLinkRepository
from craft_dashboard.repositories.issue_repository import IssueRepository

__all__ = ["IssueLinkRepository", "IssueRepository"]
