"""Issue repository queries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Integer as SAInteger
from sqlalchemy import cast, func, or_, select
from sqlalchemy import text as sa_text

from craft_dashboard.models.issue import Issue
from craft_dashboard.models.issue_activity import IssueActivity
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.models.views import IssueFilters, IssueQueryResult, IssueView

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql import Select
    from sqlalchemy.sql.elements import ColumnElement

_SCORE_SORT_FIELDS = {
    "staleness",
    "complexity",
    "support_request",
    "confidence",
}
_VALID_SORT_FIELDS = _SCORE_SORT_FIELDS | {
    "age",
    "updated",
    "title",
    "author",
    "number",
}


def _compute_age_days(created_at: datetime | None) -> int | None:
    """Compute days since creation, or None if unknown."""
    if created_at is None:
        return None
    now = datetime.now(tz=UTC)
    created = (
        created_at.replace(tzinfo=UTC) if created_at.tzinfo is None else created_at
    )
    return (now - created).days


def _apply_author_role_filter(query: Select, author_role: str) -> Select:
    """Apply author role filtering to the query."""
    if not author_role:
        return query

    role_list = [r.strip() for r in author_role.split(",") if r.strip()]
    role_conditions = []
    for role in role_list:
        if role == "maintainer":
            role_conditions.append(
                (Issue.author_is_maintainer.is_(True))
                & (Issue.author_is_bot.is_(False))
            )
        elif role == "contributor":
            role_conditions.append(
                (Issue.author_is_maintainer.is_(False))
                & (Issue.author_is_bot.is_(False))
            )
        elif role == "bot":
            role_conditions.append(Issue.author_is_bot.is_(True))
    if len(role_conditions) == 1:
        query = query.where(role_conditions[0])
    elif role_conditions:
        query = query.where(or_(*role_conditions))
    return query


def _serialize_evaluation(evaluation: LLMEvaluation) -> dict[str, Any]:
    """Serialize an evaluation for template rendering."""
    return {
        "id": evaluation.id,
        "summary": evaluation.summary,
        "suggested_action": evaluation.suggested_action,
        "suggested_action_reason": evaluation.suggested_action_reason,
        "scores": evaluation.scores or {},
        "evaluated_at": evaluation.evaluated_at,
        "model_name": evaluation.model_name,
        "tokens_used": evaluation.tokens_used,
        "prompt_tokens": evaluation.prompt_tokens,
        "completion_tokens": evaluation.completion_tokens,
        "llm_backend": evaluation.llm_backend,
        "latest": evaluation.latest,
        "has_embedding": evaluation.summary_embedding is not None,
        "eval_version": evaluation.eval_version,
        "issue_data_hash": evaluation.issue_data_hash,
    }


def _build_excluded_issues_condition(
    filtered_issues: dict[str, list[str]],
) -> ColumnElement[bool] | None:
    """Return a NOT(...) clause excluding configured issue numbers.

    Returns None when filtered_issues is empty (no WHERE clause needed).
    Requires that Issue and Project are already joined in the calling query.
    """
    conditions = [
        (Project.name == project_name) & Issue.external_id.in_(ids)
        for project_name, ids in filtered_issues.items()
        if ids
    ]
    if not conditions:
        return None
    combined = or_(*conditions) if len(conditions) > 1 else conditions[0]
    return ~combined


class IssueRepository:
    """Repository for issue-related read queries."""

    def __init__(
        self,
        session: AsyncSession,
        filtered_issues: dict[str, list[str]] | None = None,
    ) -> None:
        self.session = session
        self.filtered_issues = filtered_issues or {}

    async def get_issue_detail(
        self, project_name: str, external_id: str
    ) -> dict[str, Any] | None:
        """Get a single issue with its evaluation history."""
        if external_id in self.filtered_issues.get(project_name, []):
            return None
        issue_row = (
            await self.session.execute(
                select(
                    Issue,
                    Project.name.label("project_name"),
                    LLMEvaluation.summary,
                    LLMEvaluation.suggested_action,
                    LLMEvaluation.suggested_action_reason,
                    LLMEvaluation.scores,
                )
                .join(Project, Issue.project_id == Project.id)
                .outerjoin(
                    LLMEvaluation,
                    (LLMEvaluation.issue_id == Issue.id) & LLMEvaluation.latest,
                )
                .where(Project.name == project_name)
                .where(Issue.external_id == external_id)
            )
        ).first()
        if issue_row is None:
            return None

        issue = issue_row[0]
        evaluations = list(
            (
                await self.session.execute(
                    select(LLMEvaluation)
                    .where(LLMEvaluation.issue_id == issue.id)
                    .order_by(LLMEvaluation.evaluated_at.desc())
                )
            ).scalars()
        )
        return {
            "id": issue.id,
            "project_name": issue_row.project_name,
            "source": issue.source,
            "external_id": issue.external_id,
            "title": issue.title,
            "body": issue.body,
            "state": issue.state,
            "author": issue.author,
            "labels": list(issue.labels or []),
            "issue_type": issue.issue_type,
            "created_at": issue.created_at,
            "updated_at": issue.updated_at,
            "closed_at": issue.closed_at,
            "url": issue.url,
            "summary": issue_row.summary,
            "suggested_action": issue_row.suggested_action,
            "suggested_action_reason": issue_row.suggested_action_reason,
            "scores": issue_row.scores or {},
            "evaluation_history": [
                _serialize_evaluation(evaluation) for evaluation in evaluations
            ],
        }

    async def get_issue_activity_history(
        self, project_name: str, issue_number: str
    ) -> list[dict[str, Any]]:
        """Return the change/refresh history for a single issue or PR.

        Newest first. Returns an empty list if the project doesn't exist,
        *issue_number* isn't a valid integer, or no activity has been
        recorded yet (e.g. the issue hasn't changed since it was first
        collected).
        """
        try:
            number = int(issue_number)
        except (TypeError, ValueError):
            return []

        rows = (
            await self.session.execute(
                select(IssueActivity)
                .join(Project, Project.id == IssueActivity.project_id)
                .where(Project.name == project_name)
                .where(IssueActivity.issue_number == number)
                .order_by(IssueActivity.occurred_at.desc())
            )
        ).scalars()
        return [
            {
                "change_type": activity.change_type,
                "title": activity.title,
                "occurred_at": activity.occurred_at,
            }
            for activity in rows
        ]

    async def search(self, filters: IssueFilters) -> IssueQueryResult:
        """Query issues with filters."""
        query = (
            select(
                Issue,
                Project.name.label("project_name"),
                LLMEvaluation.summary,
                LLMEvaluation.suggested_action,
                LLMEvaluation.suggested_action_reason,
                LLMEvaluation.scores,
            )
            .join(Project, Issue.project_id == Project.id)
            .outerjoin(
                LLMEvaluation,
                (LLMEvaluation.issue_id == Issue.id) & LLMEvaluation.latest,
            )
        )

        excl = _build_excluded_issues_condition(self.filtered_issues)
        if excl is not None:
            query = query.where(excl)

        if filters.state:
            state_list = [s.strip() for s in filters.state.split(",") if s.strip()]
            if len(state_list) == 1:
                query = query.where(Issue.state == state_list[0])
            elif state_list:
                query = query.where(Issue.state.in_(state_list))

        if filters.project:
            project_list = [p.strip() for p in filters.project.split(",") if p.strip()]
            if len(project_list) == 1:
                query = query.where(Project.name == project_list[0])
            else:
                query = query.where(Project.name.in_(project_list))
        if filters.source:
            query = query.where(Issue.source == filters.source)
        if filters.issue_type:
            type_list = [t.strip() for t in filters.issue_type.split(",") if t.strip()]
            if len(type_list) == 1:
                query = query.where(Issue.issue_type == type_list[0])
            elif type_list:
                query = query.where(Issue.issue_type.in_(type_list))
        if filters.action:
            action_list = [a.strip() for a in filters.action.split(",") if a.strip()]
            if len(action_list) == 1:
                query = query.where(LLMEvaluation.suggested_action == action_list[0])
            elif action_list:
                query = query.where(LLMEvaluation.suggested_action.in_(action_list))
        query = _apply_author_role_filter(query, filters.author_role)

        if filters.search:
            tokens = filters.search.strip().split()
            for token in tokens:
                clean = token.lstrip("#")
                conditions: list[ColumnElement[bool]] = [
                    Issue.title.ilike(f"%{token}%"),
                    Issue.author.ilike(f"%{token}%"),
                    Project.name.ilike(f"%{token}%"),
                    LLMEvaluation.summary.ilike(f"%{token}%"),
                ]
                if clean.isdigit():
                    conditions.append(Issue.external_id == clean)
                query = query.where(or_(*conditions))

        if filters.llm_status == "no_llm":
            query = query.where(LLMEvaluation.id.is_(None))
        elif filters.llm_status == "partial_llm":
            query = query.where(
                LLMEvaluation.id.is_not(None)
                & (
                    LLMEvaluation.summary.is_(None)
                    | (LLMEvaluation.summary == "")
                    | (
                        (Issue.state == "open")
                        & (
                            LLMEvaluation.suggested_action.is_(None)
                            | (LLMEvaluation.suggested_action == "")
                            | LLMEvaluation.scores.is_(None)
                        )
                    )
                )
            )

        count_query = select(func.count()).select_from(query.subquery())
        total = await self.session.scalar(count_query) or 0
        if filters.items_per_page <= 0:
            total_pages = 1
            page = 1
        else:
            total_pages = max(
                1, (total + filters.items_per_page - 1) // filters.items_per_page
            )
            page = min(filters.page, total_pages)

        sort_field = filters.sort_by.lstrip("-")
        sort_desc = filters.sort_by.startswith("-")

        if sort_field not in _VALID_SORT_FIELDS:
            sort_field = "staleness"
            sort_desc = False

        if sort_field == "age":
            col = Issue.created_at
            query = query.order_by(col.asc() if not sort_desc else col.desc())
        elif sort_field == "updated":
            col = Issue.updated_at
            query = query.order_by(col.desc() if not sort_desc else col.asc())
        elif sort_field == "title":
            col = Issue.title
            query = query.order_by(col.asc() if not sort_desc else col.desc())
        elif sort_field == "author":
            col = Issue.author
            query = query.order_by(col.asc() if not sort_desc else col.desc())
        elif sort_field == "number":
            numeric_id = cast(Issue.external_id, SAInteger)
            if sort_desc:
                query = query.order_by(Project.name.desc(), numeric_id.desc())
            else:
                query = query.order_by(Project.name.asc(), numeric_id.asc())
        elif sort_field in _SCORE_SORT_FIELDS:
            score_order = func.coalesce(LLMEvaluation.scores[sort_field].as_float(), 0)
            query = query.order_by(
                score_order.asc() if sort_desc else score_order.desc()
            )

        if filters.items_per_page > 0:
            offset = (page - 1) * filters.items_per_page
            query = query.offset(offset).limit(filters.items_per_page)

        result = await self.session.execute(query)

        issues = []
        for row in result:
            issue = row[0]
            scores = row.scores or {}
            issues.append(
                IssueView(
                    id=issue.id,
                    project_name=row.project_name,
                    source=issue.source,
                    external_id=issue.external_id,
                    title=issue.title,
                    author=issue.author,
                    issue_type=issue.issue_type,
                    state=issue.state,
                    url=issue.url,
                    summary=row.summary,
                    suggested_action=row.suggested_action,
                    suggested_action_reason=row.suggested_action_reason,
                    scores=scores,
                    age_days=_compute_age_days(issue.created_at),
                    labels=list(issue.labels or []),
                    created_at=issue.created_at,
                    updated_at=issue.updated_at,
                    author_is_maintainer=issue.author_is_maintainer,
                    author_is_bot=issue.author_is_bot,
                    staleness=scores.get("staleness"),
                    complexity=scores.get("complexity"),
                    support_request=scores.get("support_request"),
                    confidence=scores.get("confidence"),
                )
            )

        return IssueQueryResult(
            issues=issues,
            total_count=total,
            total_pages=total_pages,
            page=page,
        )

    async def find_similar_issues(
        self,
        *,
        issue_id: int,
        top_n: int = 10,
        similarity_threshold: float = 0.70,
    ) -> list[dict[str, Any]]:
        """Return up to top_n issues most similar to issue_id by cosine similarity.

        Uses pgvector's <=> (cosine distance) operator via the HNSW index on
        llm_evaluations.summary_embedding. Returns [] when the issue has no
        embedding (e.g. all SQLite test fixtures, or issues not yet evaluated
        with an embedding model).
        """
        embedding_row = await self.session.execute(
            select(LLMEvaluation.summary_embedding)
            .where(LLMEvaluation.issue_id == issue_id)
            .where(LLMEvaluation.latest)
        )
        embedding = embedding_row.scalar_one_or_none()
        if embedding is None:
            return []

        distance_threshold = 1.0 - similarity_threshold

        sql = sa_text("""
            SELECT
                i.id          AS id,
                i.external_id AS external_id,
                i.title       AS title,
                i.url         AS url,
                i.state       AS state,
                p.name        AS project_name,
                e.summary     AS summary,
                (e.summary_embedding <=> CAST(:embedding AS vector)) AS distance
            FROM llm_evaluations e
            JOIN issues i ON i.id = e.issue_id
            JOIN projects p ON p.id = i.project_id
            WHERE e.latest = true
              AND e.summary_embedding IS NOT NULL
              AND e.issue_id != :issue_id
              AND (e.summary_embedding <=> CAST(:embedding AS vector)) < :distance_threshold
            ORDER BY distance
            LIMIT :limit
        """)

        result = await self.session.execute(
            sql,
            {
                "embedding": str(list(embedding)),
                "issue_id": issue_id,
                "distance_threshold": distance_threshold,
                "limit": top_n,
            },
        )

        return [
            {
                "id": row.id,
                "external_id": row.external_id,
                "title": row.title,
                "url": row.url,
                "state": row.state,
                "project_name": row.project_name,
                "summary": row.summary,
                "similarity": round(1.0 - row.distance, 3),
            }
            for row in result
        ]

    async def get_project_names(self) -> list[str]:
        """Get non-aggregate project names with issues, ordered by display_order."""
        rows = await self.session.execute(
            select(Project.name, Project.display_order)
            .join(Issue, Issue.project_id == Project.id)
            .where(Project.category != "aggregate")
            .distinct()
            .order_by(Project.display_order, Project.name)
        )
        return [row.name for row in rows]
