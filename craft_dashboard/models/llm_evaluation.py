"""LLM evaluation model for issue/PR scoring."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from craft_dashboard.models.base import Base


class LLMEvaluation(Base):
    """An LLM-generated evaluation of an issue or pull request.

    Multiple evaluations can exist per issue (history is kept). The most
    recent evaluation has latest=True; all previous ones have latest=False.
    A partial unique index enforces that only one row per issue has latest=True.
    """

    __tablename__ = "llm_evaluations"
    __table_args__ = (
        Index(
            "ix_llm_evaluations_latest_issue",
            "issue_id",
            unique=True,
            postgresql_where="latest = true",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("issues.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_action: Mapped[str | None] = mapped_column(String(50), nullable=True)
    suggested_action_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    scores: Mapped[dict] = mapped_column(JSONB, default=dict)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    issue_data_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    issue: Mapped["Issue"] = relationship(back_populates="evaluations")  # noqa: F821

    def __repr__(self) -> str:
        """Return a string representation."""
        return (
            f"<LLMEvaluation(issue_id={self.issue_id}, "
            f"action={self.suggested_action!r}, latest={self.latest})>"
        )
