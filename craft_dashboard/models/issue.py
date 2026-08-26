"""Issue and Pull Request model."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from craft_dashboard.models.base import Base

if TYPE_CHECKING:
    from craft_dashboard.models.collection_run import CollectionRun
    from craft_dashboard.models.llm_evaluation import LLMEvaluation
    from craft_dashboard.models.project import Project


class Issue(Base):
    """An issue or pull request from GitHub or Launchpad."""

    __tablename__ = "issues"
    __table_args__ = (
        UniqueConstraint("project_id", "source", "external_id"),
        Index("ix_issues_content_hash", "content_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_is_maintainer: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    author_is_bot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    labels: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    comments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True, default=list
    )
    # SHA-256 hash of title/body/state/labels/comments/PR-review-details, kept
    # up to date by the collectors on every create/update (see
    # craft_dashboard.llm.content_hash.compute_content_hash). Comparing this
    # against LLMEvaluation.issue_data_hash is how "does this issue need
    # re-evaluation" is detected without recomputing a hash for every issue
    # on every poll.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Incremented by the commit scanner whenever a new commit implicates
    # this issue (qualified ref, path intersection, semantic match, or bare
    # ref — see craft_dashboard.commit_scanner). Compared against
    # LLMEvaluation.evidence_generation the same way content_hash is
    # compared against LLMEvaluation.issue_data_hash: a cheap integer
    # column comparison, never a per-row recomputation.
    evidence_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # OpenRouter embedding (openai/text-embedding-3-small, 1024 dims) of
    # f"{title}\n\n{body}", used for semantic search over issue titles and
    # descriptions. Shares the same vector space as
    # LLMEvaluation.summary_embedding. Populated by the one-time
    # scripts/backfill_search_embeddings.py backfill and kept fresh going
    # forward by scripts/llm/eval_worker.py whenever content_hash changes.
    search_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1024), nullable=True
    )
    last_fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    collection_run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("collection_runs.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="issues")
    collection_run: Mapped["CollectionRun | None"] = relationship(
        back_populates="issues"
    )
    evaluations: Mapped[list["LLMEvaluation"]] = relationship(
        back_populates="issue",
        order_by="LLMEvaluation.evaluated_at.desc()",
    )

    @property
    def latest_evaluation(self) -> "LLMEvaluation | None":
        """Return the most recent LLM evaluation, or None."""
        return next((e for e in self.evaluations if e.latest), None)

    def __repr__(self) -> str:
        """Return a string representation."""
        return f"<Issue(source={self.source!r}, external_id={self.external_id!r}, title={self.title!r})>"
