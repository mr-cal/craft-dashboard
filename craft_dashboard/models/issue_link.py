"""Issue link model: structured relationships between issues.

Populated by the evaluator's ``related_work`` output (Phase 6) and by
``DuplicateDetector`` (see ``craft_dashboard.repositories.issue_link_repository``),
this table subsumes duplicate detection: a duplicate finding is simply a row
with ``kind="duplicate_of"`` and ``source="duplicate_detector"``.

Links are owned by the evaluation that produced them. Re-evaluating an issue
does not accumulate links — only links from the issue's current *latest*
evaluation are considered current; prior links are retained for history but
should be filtered out by callers (join on ``LLMEvaluation.latest``) rather
than deleted, mirroring the existing ``latest`` flag convention on
``LLMEvaluation`` itself.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from craft_dashboard.models.base import Base

if TYPE_CHECKING:
    from craft_dashboard.models.issue import Issue
    from craft_dashboard.models.llm_evaluation import LLMEvaluation

#: Valid values for IssueLink.kind.
LINK_KINDS = (
    "likely_fixed_by",
    "blocked_by",
    "duplicate_of",
    "related_to",
    "caused_by",
)

#: Valid values for IssueLink.source.
LINK_SOURCES = ("evaluator", "duplicate_detector")


class IssueLink(Base):
    """A structured, confidence-scored relationship from one issue to another."""

    __tablename__ = "issue_links"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('likely_fixed_by', 'blocked_by', 'duplicate_of', "
            "'related_to', 'caused_by')",
            name="ck_issue_links_kind",
        ),
        CheckConstraint(
            "source IN ('evaluator', 'duplicate_detector')",
            name="ck_issue_links_source",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name="ck_issue_links_confidence_range",
        ),
        # The dominant read pattern is "give me this issue's current links",
        # scoped through the owning evaluation's `latest` flag — see
        # get_latest_links_for_issue(). Indexing from_issue_id directly (not
        # a composite with llm_evaluation_id) keeps that lookup a single
        # index scan regardless of how many historical evaluations exist.
        Index("ix_issue_links_from_issue_id", "from_issue_id"),
        Index("ix_issue_links_to_issue_id", "to_issue_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_issue_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("issues.id", ondelete="CASCADE"), nullable=False
    )
    llm_evaluation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("llm_evaluations.id", ondelete="CASCADE"), nullable=False
    )
    # Nullable: a reference the model or duplicate detector names (e.g.
    # "canonical/craft-parts#567") may not resolve to a known Issue row —
    # the raw ref is always retained in to_ref regardless.
    to_issue_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("issues.id", ondelete="SET NULL"), nullable=True
    )
    to_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    from_issue: Mapped["Issue"] = relationship(foreign_keys=[from_issue_id])
    to_issue: Mapped["Issue | None"] = relationship(foreign_keys=[to_issue_id])
    llm_evaluation: Mapped["LLMEvaluation"] = relationship()

    def __repr__(self) -> str:
        """Return a string representation."""
        return f"<IssueLink(kind={self.kind!r}, to_ref={self.to_ref!r})>"
