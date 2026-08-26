"""Evaluation transcript model: what the tool-calling loop actually did.

One row per LLMEvaluation, not one row per tool call — ``rounds`` is a JSONB
list, one entry per tool-calling round. Kept in a separate table from
``llm_evaluations`` (rather than as columns on it) so the hot, frequently
queried ``llm_evaluations`` table (521MB / 81,099 rows already) stays narrow
and retention deletes here never touch it. See
``craft_dashboard.services.transcript_gc`` for the retention policy.

Phase 1 intentionally adds only the persistence shape. The Phase 4/6 writer
follow-up must introduce ``EVAL_TRANSCRIPT_FULL`` wiring in Settings and
``.env.example`` when the tool-calling loop actually reads the flag.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from craft_dashboard.models.base import Base


class EvaluationTranscript(Base):
    """Recorded tool-calling activity for one LLMEvaluation.

    ``rounds`` holds, per tool-calling round, in compact form by default:
    tool name, arguments, the result's SHA-256, its byte count, the first
    ~500 characters of the result, and the model's reasoning text for that
    round (when the backend/model provides it). When ``full_capture`` is
    True (``EVAL_TRANSCRIPT_FULL=1``, used during pilot/refinement runs
    only), the untruncated result is stored instead of the ~500-character
    preview.
    """

    __tablename__ = "evaluation_transcripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    llm_evaluation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("llm_evaluations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rounds: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    full_capture: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    rounds_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        """Return a string representation."""
        return (
            f"<EvaluationTranscript(llm_evaluation_id={self.llm_evaluation_id}, "
            f"rounds_used={self.rounds_used})>"
        )
