"""Evaluation API token model for authenticated workers and operators."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from craft_dashboard.models.base import Base

if TYPE_CHECKING:
    from craft_dashboard.models.llm_evaluation import LLMEvaluation


class EvalApiToken(Base):
    """An API token authorized to claim issues, post evaluations, or perform admin actions."""

    __tablename__ = "eval_api_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    permissions: Mapped[list[str]] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    evaluations: Mapped[list["LLMEvaluation"]] = relationship(
        back_populates="eval_token",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        """Return a string representation."""
        return f"<EvalApiToken(id={self.id!r}, active={self.is_active}, prefix={self.token_prefix!r})>"
