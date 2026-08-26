"""Repository for IssueLink: structured issue-to-issue relationships."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from craft_dashboard.models.issue_link import IssueLink
from craft_dashboard.models.llm_evaluation import LLMEvaluation

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class IssueLinkRepository:
    """Read/write access to issue_links."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_from_duplicate_check(
        self,
        *,
        from_issue_id: int,
        llm_evaluation_id: int,
        duplicate_result: dict[str, Any],
    ) -> IssueLink | None:
        """Persist a confirmed DuplicateDetector.check_duplicates() finding.

        Args:
            from_issue_id: DB id of the issue that was checked.
            llm_evaluation_id: The evaluation that produced this finding.
            duplicate_result: The dict returned by
                ``DuplicateDetector.check_duplicates()``. If it lacks a
                ``duplicate_of_issue_id`` key (no confirmed duplicate), no
                row is written and ``None`` is returned.

        Returns:
            The created (and flushed, but not committed) IssueLink, or None
            if duplicate_result carries no confirmed duplicate.

        """
        if "duplicate_of_issue_id" not in duplicate_result:
            return None

        to_ref = (
            f"{duplicate_result['duplicate_of_project_name']}"
            f"#{duplicate_result['duplicate_of_external_id']}"
        )
        link = IssueLink(
            from_issue_id=from_issue_id,
            llm_evaluation_id=llm_evaluation_id,
            to_issue_id=duplicate_result["duplicate_of_issue_id"],
            to_ref=to_ref,
            kind="duplicate_of",
            confidence=duplicate_result.get("confidence", 0),
            note=duplicate_result.get("reason") or None,
            source="duplicate_detector",
        )
        self.session.add(link)
        await self.session.flush()
        return link

    async def get_latest_links_for_issue(self, issue_id: int) -> list[IssueLink]:
        """Return links produced by *issue_id*'s current (latest) evaluation only.

        Prior evaluations' links are retained in the table for history/audit
        but are never returned here, mirroring the existing ``latest`` flag
        convention on ``LLMEvaluation``.
        """
        query = (
            select(IssueLink)
            .join(LLMEvaluation, IssueLink.llm_evaluation_id == LLMEvaluation.id)
            .where(IssueLink.from_issue_id == issue_id)
            .where(LLMEvaluation.latest)
            .order_by(IssueLink.confidence.desc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())
