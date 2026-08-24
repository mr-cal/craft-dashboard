"""Engagement routes: Discourse forum activity trend graphs.

See plans/33-forum-activity-tracker.md for the full design.
"""

import json
from collections import defaultdict
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from craft_dashboard.config import DashboardConfig
from craft_dashboard.dependencies import get_config, get_db_session
from craft_dashboard.models.forum import ForumBackfillState, ForumTopic

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/engagement")


@router.get("/forums", response_class=HTMLResponse)
async def forums_page(
    request: Request,
    config: DashboardConfig = Depends(get_config),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the forum activity page with one Chart.js graph per forum."""
    templates: Jinja2Templates = request.app.state.templates

    forums: list[dict] = []
    for name, forum_config in config.forums.items():
        state = await session.scalar(
            select(ForumBackfillState).where(ForumBackfillState.forum == name)
        )
        categories = sorted(state.categories_cache) if state else []
        forums.append(
            {
                "name": name,
                "base_url": forum_config.base_url,
                "categories": categories,
                "default_categories": list(forum_config.default_categories),
            }
        )

    return templates.TemplateResponse(
        request,
        "engagement/forums.html",
        {"forums": forums, "forums_json": json.dumps(forums)},
    )


@router.get("/forums/data", response_class=JSONResponse)
async def forums_data(
    session: AsyncSession = Depends(get_db_session),
    forum: str = Query(...),
) -> JSONResponse:
    """Return monthly post-count series for a forum, one per category plus "all".

    Buckets every topic by the month it was created in and sums
    ``posts_count`` (the topic's total reply count as of the last
    collector run) into that month — matching the topic-level-aggregate
    storage model described in plans/33-forum-activity-tracker.md (no
    individual post bodies/timestamps are stored). Returns every category's
    series in one response so the frontend can toggle visibility locally
    without a round trip per checkbox change.
    """
    exists = await session.scalar(
        select(ForumBackfillState.id).where(ForumBackfillState.forum == forum).limit(1)
    )
    if exists is None:
        exists = await session.scalar(
            select(ForumTopic.id).where(ForumTopic.forum == forum).limit(1)
        )
    if exists is None:
        raise HTTPException(status_code=404, detail=f"Forum '{forum}' not found")

    result = await session.execute(
        select(
            ForumTopic.created_at, ForumTopic.posts_count, ForumTopic.category
        ).where(ForumTopic.forum == forum)
    )

    all_by_month: dict[str, int] = defaultdict(int)
    category_by_month: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for row in result:
        month_key = row.created_at.strftime("%Y-%m")
        all_by_month[month_key] += row.posts_count
        category_by_month[row.category][month_key] += row.posts_count

    months = sorted(all_by_month)
    all_series = [all_by_month[m] for m in months]
    categories_series = {
        category: [by_month.get(m, 0) for m in months]
        for category, by_month in sorted(category_by_month.items())
    }

    return JSONResponse(
        {"months": months, "all": all_series, "categories": categories_series}
    )
