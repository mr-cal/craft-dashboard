"""Evaluation API token and evaluation clearance queries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, or_, select, update

from craft_dashboard.models.eval_token import EvalApiToken
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql import Select


class EvalTokenRepository:
    """Repository for managing evaluation tokens and evaluation query/clearance operations."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with database session."""
        self.session = session

    async def get_by_id(self, token_id: str) -> EvalApiToken | None:
        """Retrieve an evaluation token by its unique identifier."""
        query = select(EvalApiToken).where(EvalApiToken.id == token_id)
        return await self.session.scalar(query)

    async def get_by_hash(self, token_hash: str) -> EvalApiToken | None:
        """Retrieve an active or revoked evaluation token by SHA-256 token hash."""
        query = select(EvalApiToken).where(EvalApiToken.token_hash == token_hash)
        return await self.session.scalar(query)

    async def list_tokens(self, *, include_revoked: bool = True) -> list[EvalApiToken]:
        """List evaluation tokens, optionally including revoked tokens."""
        query = select(EvalApiToken)
        if not include_revoked:
            query = query.where(EvalApiToken.is_active.is_(True))
        query = query.order_by(EvalApiToken.created_at.asc())
        result = await self.session.execute(query)
        scalars = result.scalars()
        return list(scalars.all() if hasattr(scalars, "all") else scalars)

    async def create_token(
        self,
        token_id: str,
        name: str,
        token_hash: str,
        token_prefix: str,
        permissions: list[str],
    ) -> EvalApiToken:
        """Create and store a new evaluation token."""
        token = EvalApiToken(
            id=token_id,
            name=name,
            token_hash=token_hash,
            token_prefix=token_prefix,
            permissions=permissions,
            is_active=True,
        )
        self.session.add(token)
        return token

    async def revoke_token(self, token_id: str) -> bool:
        """Revoke an evaluation token by setting is_active=False and revoked_at=now."""
        now = datetime.now(tz=UTC)
        statement = (
            update(EvalApiToken)
            .where(EvalApiToken.id == token_id, EvalApiToken.is_active.is_(True))
            .values(is_active=False, revoked_at=now)
        )
        result = await self.session.execute(statement)
        return bool(getattr(result, "rowcount", 0) > 0)

    async def update_last_used(self, token_id: str, when: datetime) -> None:
        """Update last_used_at timestamp for a token."""
        statement = (
            update(EvalApiToken)
            .where(EvalApiToken.id == token_id)
            .values(last_used_at=when)
        )
        await self.session.execute(statement)

    async def get_evaluation_counts_by_token(self) -> dict[str, int]:
        """Return counts of LLM evaluations grouped by evaluated_by token ID."""
        query = select(
            LLMEvaluation.evaluated_by, func.count(LLMEvaluation.id)
        ).group_by(LLMEvaluation.evaluated_by)
        result = await self.session.execute(query)
        if hasattr(result, "tuples"):
            rows = result.tuples().all()
        elif hasattr(result, "all"):
            rows = result.all()
        else:
            rows = list(result)
        return {(evaluated_by or "unattributed"): count for evaluated_by, count in rows}

    def _build_evaluation_filter_query(
        self,
        *,
        project: str | None = None,
        issue_number: str | None = None,
        evaluated_by: str | None = None,
        stale_days: int | None = None,
        incomplete: bool = False,
        now: datetime | None = None,
    ) -> Select[Any]:
        """Build a select query for evaluation IDs matching given criteria."""
        query = select(LLMEvaluation.id)

        needs_issue_join = bool(project or issue_number)
        needs_project_join = bool(project)

        if needs_project_join:
            query = query.join(Issue, LLMEvaluation.issue_id == Issue.id).join(
                Project, Issue.project_id == Project.id
            )
            query = query.where(Project.name == project)
        elif needs_issue_join:
            query = query.join(Issue, LLMEvaluation.issue_id == Issue.id)

        if issue_number:
            query = query.where(Issue.external_id == str(issue_number))

        if evaluated_by:
            query = query.where(LLMEvaluation.evaluated_by == evaluated_by)

        if stale_days and stale_days > 0:
            current_time = now or datetime.now(tz=UTC)
            stale_threshold = current_time - timedelta(days=stale_days)
            query = query.where(LLMEvaluation.evaluated_at < stale_threshold)

        if incomplete:
            query = query.where(
                or_(
                    LLMEvaluation.model_name == "pending",
                    LLMEvaluation.summary.is_(None),
                    LLMEvaluation.suggested_action.is_(None),
                )
            )

        return query

    async def count_evaluations(
        self,
        *,
        project: str | None = None,
        issue_number: str | None = None,
        evaluated_by: str | None = None,
        stale_days: int | None = None,
        incomplete: bool = False,
        now: datetime | None = None,
    ) -> int:
        """Count evaluations matching the specified filters."""
        matching_ids = self._build_evaluation_filter_query(
            project=project,
            issue_number=issue_number,
            evaluated_by=evaluated_by,
            stale_days=stale_days,
            incomplete=incomplete,
            now=now,
        )
        count_query = select(func.count()).select_from(matching_ids.subquery())
        return await self.session.scalar(count_query) or 0

    async def delete_evaluations(
        self,
        *,
        project: str | None = None,
        issue_number: str | None = None,
        evaluated_by: str | None = None,
        stale_days: int | None = None,
        incomplete: bool = False,
        now: datetime | None = None,
    ) -> int:
        """Delete evaluations matching the specified filters."""
        matching_ids = self._build_evaluation_filter_query(
            project=project,
            issue_number=issue_number,
            evaluated_by=evaluated_by,
            stale_days=stale_days,
            incomplete=incomplete,
            now=now,
        )
        statement = delete(LLMEvaluation).where(LLMEvaluation.id.in_(matching_ids))
        result = await self.session.execute(statement)
        return getattr(result, "rowcount", 0) or 0
