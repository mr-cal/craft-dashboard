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
                "display_name": forum_config.display_name or f"{name} forum",
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
    """Return daily new-topic-count series for a forum, one per category plus "all".

    Buckets every topic by the day it was created and counts the number of
    new topics created that day, per category (plus an "all categories"
    series). This uses each topic's exact creation timestamp, which is
    precise, unlike ``posts_count`` (a topic's total reply count as of the
    last collector run, not tied to when individual replies happened).
    Returns every category's series in one response so the frontend can
    toggle visibility locally without a round trip per checkbox change.
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
        select(ForumTopic.created_at, ForumTopic.category).where(
            ForumTopic.forum == forum
        )
    )

    all_by_day: dict[str, int] = defaultdict(int)
    category_by_day: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for row in result:
        day_key = row.created_at.strftime("%Y-%m-%d")
        all_by_day[day_key] += 1
        category_by_day[row.category][day_key] += 1

    days = sorted(all_by_day)
    all_series = [all_by_day[d] for d in days]
    categories_series = {
        category: [by_day.get(d, 0) for d in days]
        for category, by_day in sorted(category_by_day.items())
    }

    return JSONResponse(
        {"days": days, "all": all_series, "categories": categories_series}
    )
