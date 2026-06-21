"""Persistence helpers for LLM evaluation results."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.llm.evaluator import CURRENT_EVAL_VERSION
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.settings import Settings
from sqlalchemy import delete, false, func, select, update
from sqlalchemy.dialects.postgresql import insert

if TYPE_CHECKING:
    from craft_dashboard.llm.evaluator import EvaluationResult
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


def _resolve_session_resources(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    engine: AsyncEngine | None = None,
) -> tuple[async_sessionmaker[AsyncSession], AsyncEngine, bool]:
    if session_factory is not None and engine is not None:
        return session_factory, engine, False

    settings = Settings()
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)
    return session_factory, engine, True


async def count_evaluations(
    project: str = "",
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    engine: AsyncEngine | None = None,
) -> int:
    """Count stored LLM evaluations, optionally scoped to one project."""
    session_factory, engine, owns_engine = _resolve_session_resources(
        session_factory, engine
    )

    try:
        async with session_factory() as session:
            if project:
                count_query = (
                    select(func.count(LLMEvaluation.id))
                    .join(Issue, LLMEvaluation.issue_id == Issue.id)
                    .join(Project, Issue.project_id == Project.id)
                    .where(Project.name == project)
                )
            else:
                count_query = select(func.count(LLMEvaluation.id))

            return await session.scalar(count_query) or 0
    finally:
        if owns_engine:
            await engine.dispose()


async def store_evaluation_result(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    issue_id: int,
    result: EvaluationResult,
    model: str,
    llm_backend: str,
) -> None:
    """Store a fresh LLM evaluation and mark it as the latest row."""
    insert_stmt = insert(LLMEvaluation).values(
        issue_id=issue_id,
        model_name=model,
        eval_version=CURRENT_EVAL_VERSION,
        summary=result["summary"],
        suggested_action=result["suggested_action"],
        suggested_action_reason=result["suggested_action_reason"],
        scores=result["scores"],
        tokens_used=result["tokens_used"],
        prompt_tokens=result["prompt_tokens"],
        completion_tokens=result["completion_tokens"],
        llm_backend=llm_backend,
        evaluated_at=datetime.now(tz=UTC),
        issue_data_hash=result["issue_data_hash"],
        latest=True,
    )
    insert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=[LLMEvaluation.issue_id],
        index_where=LLMEvaluation.latest.is_(True),
        set_={
            "model_name": insert_stmt.excluded.model_name,
            "eval_version": insert_stmt.excluded.eval_version,
            "summary": insert_stmt.excluded.summary,
            "suggested_action": insert_stmt.excluded.suggested_action,
            "suggested_action_reason": insert_stmt.excluded.suggested_action_reason,
            "scores": insert_stmt.excluded.scores,
            "tokens_used": insert_stmt.excluded.tokens_used,
            "prompt_tokens": insert_stmt.excluded.prompt_tokens,
            "completion_tokens": insert_stmt.excluded.completion_tokens,
            "llm_backend": insert_stmt.excluded.llm_backend,
            "evaluated_at": insert_stmt.excluded.evaluated_at,
            "issue_data_hash": insert_stmt.excluded.issue_data_hash,
            "latest": insert_stmt.excluded.latest,
        },
    )

    async with session_factory() as session:
        await session.execute(
            update(LLMEvaluation)
            .where(LLMEvaluation.issue_id == issue_id, LLMEvaluation.latest.is_(True))
            .values(latest=false())
        )
        await session.execute(insert_stmt)
        await session.commit()


async def _clear_evaluations(
    project: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    engine: AsyncEngine | None = None,
) -> int:
    """Clear stored LLM evaluations and return deleted row count."""
    session_factory, engine, owns_engine = _resolve_session_resources(
        session_factory, engine
    )

    try:
        async with session_factory() as session:
            if project:
                issue_ids = (
                    select(Issue.id)
                    .join(Project, Issue.project_id == Project.id)
                    .where(Project.name == project)
                )
                statement = delete(LLMEvaluation).where(
                    LLMEvaluation.issue_id.in_(issue_ids)
                )
            else:
                statement = delete(LLMEvaluation)

            result = await session.execute(statement)
            await session.commit()
            return result.rowcount or 0
    finally:
        if owns_engine:
            await engine.dispose()
