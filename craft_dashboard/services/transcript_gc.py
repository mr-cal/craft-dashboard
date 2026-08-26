"""Retention garbage collection for evaluation_transcripts.

Transcripts belonging to an evaluation that is still ``latest=True`` are
kept indefinitely. Transcripts belonging to a superseded (``latest=False``)
evaluation are deleted once older than ``retention_days``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, select

from craft_dashboard.models.evaluation_transcript import EvaluationTranscript
from craft_dashboard.models.llm_evaluation import LLMEvaluation

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_TRANSCRIPT_RETENTION_DAYS = 30


async def delete_superseded_transcripts(
    session: AsyncSession,
    *,
    retention_days: int = DEFAULT_TRANSCRIPT_RETENTION_DAYS,
) -> int:
    """Delete transcripts of superseded evaluations older than retention_days."""
    if retention_days < 0:
        raise ValueError("retention_days must be non-negative")

    cutoff = datetime.now(tz=UTC) - timedelta(days=retention_days)
    result = cast(
        Any,
        await session.execute(
            delete(EvaluationTranscript)
            .where(EvaluationTranscript.created_at < cutoff)
            .where(
                EvaluationTranscript.llm_evaluation_id.in_(
                    select(LLMEvaluation.id).where(~LLMEvaluation.latest)
                )
            )
        ),
    )
    await session.commit()
    return result.rowcount or 0
