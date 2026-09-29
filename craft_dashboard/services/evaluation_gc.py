"""Retention garbage collection for llm_evaluations.

Every read path filters on ``latest``, so a superseded evaluation is only an
audit record. Two costs make that record expensive:

* its ``summary_embedding`` still occupies the pgvector index, even though
  semantic search only ever queries ``latest`` rows, and
* the row itself is never read again.

``clear_superseded_embeddings`` addresses the first without losing any
readable history; ``delete_superseded_evaluations`` addresses the second once
the record is older than the retention window. Transcripts cascade on delete.

Both work in batches and commit per batch: the database holds far more
superseded rows than a single transaction should touch at once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func, select, update

from craft_dashboard.models.llm_evaluation import LLMEvaluation

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_EVALUATION_RETENTION_DAYS = 90
DEFAULT_BATCH_SIZE = 5000


async def clear_superseded_embeddings(
    session: AsyncSession,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
) -> int:
    """Null ``summary_embedding`` on superseded evaluations.

    Returns the number of rows affected, or the number that would be affected
    when ``dry_run`` is set.
    """
    if batch_size < 1:
        msg = "batch_size must be positive"
        raise ValueError(msg)

    base = (~LLMEvaluation.latest) & LLMEvaluation.summary_embedding.isnot(None)

    if dry_run:
        return int(
            await session.scalar(
                select(func.count()).select_from(LLMEvaluation).where(base)
            )
            or 0
        )

    total = 0
    while True:
        ids = (
            await session.scalars(
                select(LLMEvaluation.id).where(base).limit(batch_size)
            )
        ).all()
        if not ids:
            break
        result = cast(
            Any,
            await session.execute(
                update(LLMEvaluation)
                .where(LLMEvaluation.id.in_(ids))
                .values(summary_embedding=None)
            ),
        )
        await session.commit()
        total += result.rowcount or 0
    return total


async def delete_superseded_evaluations(
    session: AsyncSession,
    *,
    retention_days: int = DEFAULT_EVALUATION_RETENTION_DAYS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
) -> int:
    """Delete superseded evaluations older than ``retention_days``.

    An evaluation that is still ``latest`` is never deleted, however old it
    is, so every issue keeps its current evaluation.
    """
    if retention_days < 0:
        msg = "retention_days must be non-negative"
        raise ValueError(msg)
    if batch_size < 1:
        msg = "batch_size must be positive"
        raise ValueError(msg)

    cutoff = datetime.now(tz=UTC) - timedelta(days=retention_days)
    base = (~LLMEvaluation.latest) & (LLMEvaluation.evaluated_at < cutoff)

    if dry_run:
        return int(
            await session.scalar(
                select(func.count()).select_from(LLMEvaluation).where(base)
            )
            or 0
        )

    total = 0
    while True:
        ids = (
            await session.scalars(
                select(LLMEvaluation.id).where(base).limit(batch_size)
            )
        ).all()
        if not ids:
            break
        # Load and delete via the ORM so the transcript cascade is honoured
        # on SQLite, which does not enforce ON DELETE CASCADE by default.
        rows = (
            await session.scalars(
                select(LLMEvaluation).where(LLMEvaluation.id.in_(ids))
            )
        ).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        total += len(rows)
    return total
